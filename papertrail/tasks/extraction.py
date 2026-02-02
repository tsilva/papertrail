"""Extraction and classification tasks."""

import fcntl
import json
import time as _time
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from papertrail.logging_utils import (
    setup_failure_logger,
    log_failure,
    get_logger,
    DocumentLogger,
    setup_task_logging,
)
from papertrail.models import DocumentMetadata, DocumentMetadataRaw
from papertrail.llm import (
    get_system_prompt_raw_extraction,
    TOOLS_RAW_EXTRACTION,
    normalize_metadata,
)
from papertrail.pdf import render_pdf_to_images, find_pdf_files, get_page_count
from papertrail.metadata import build_hash_index, save_metadata_json
from papertrail.hashing import hash_file_fast, hash_file_content
from papertrail.tasks import task_log_context

logger = get_logger('cli')


def classify_pdf_document(pdf_path: Path, file_hash: str, failure_logger=None,
                          doc_logger: DocumentLogger = None) -> DocumentMetadata:
    """Classify a PDF document using the LLM."""
    import main
    ctx = main.get_ctx()

    if doc_logger:
        doc_logger.start_document(pdf_path)

    try:
        t0 = _time.monotonic()
        images_b64 = render_pdf_to_images(pdf_path)
        if doc_logger:
            doc_logger.log_timing("pdf_render", _time.monotonic() - t0)
    except Exception as e:
        log_failure(failure_logger, pdf_path, e)
        if doc_logger:
            doc_logger.end_document("FAILED")
        raise RuntimeError(f"Failed to render PDF image: {pdf_path}") from e

    try:
        user_content = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
            }
            for img_b64 in images_b64
        ]

        messages = [
            {"role": "system", "content": get_system_prompt_raw_extraction()},
            {"role": "user", "content": user_content},
        ]

        t0 = _time.monotonic()
        response = ctx.openai_client.chat.completions.create(
            model=ctx.model_id,
            max_tokens=4096,
            temperature=0,
            messages=messages,
            tools=TOOLS_RAW_EXTRACTION,
            tool_choice={"type": "function", "function": {"name": "extract_document_metadata"}},
        )
        if doc_logger:
            doc_logger.log_timing("llm_extraction", _time.monotonic() - t0)
            if response.usage:
                doc_logger.log_llm_usage(ctx.model_id, response.usage.prompt_tokens, response.usage.completion_tokens)

        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls:
            raise ValueError("OpenRouter did not return structured classification.")

        args = tool_calls[0].function.arguments
        raw_metadata = DocumentMetadataRaw.model_validate_json(args)

        if doc_logger:
            doc_logger.log_extraction(raw_metadata.model_dump())

        t0 = _time.monotonic()
        normalized_doc_type, normalized_issuing_party = normalize_metadata(
            raw_metadata, ctx.openai_client, ctx.model_id, mappings=ctx.mappings_manager,
            doc_logger=doc_logger,
        )
        if doc_logger:
            doc_logger.log_timing("normalization", _time.monotonic() - t0)

        metadata = DocumentMetadata(
            issue_date=raw_metadata.issue_date,
            document_type=normalized_doc_type,
            issuing_party=normalized_issuing_party,
            service_name=raw_metadata.service_name,
            total_amount=raw_metadata.total_amount,
            total_amount_currency=raw_metadata.total_amount_currency,
            confidence=raw_metadata.confidence,
            reasoning=raw_metadata.reasoning,
            content_hash=file_hash,
            document_type_raw=raw_metadata.document_type,
            issuing_party_raw=raw_metadata.issuing_party,
        )

        now = datetime.now().strftime("%Y-%m-%d")
        metadata.create_date = now
        metadata.update_date = now
        metadata.page_count = get_page_count(pdf_path)

        if doc_logger:
            doc_logger.log_final(metadata.model_dump())
            doc_logger.end_document("SUCCESS")

        return metadata
    except Exception as e:
        log_failure(failure_logger, pdf_path, e)
        if doc_logger:
            doc_logger.end_document("FAILED")
        raise RuntimeError(f"Classification failed for: {pdf_path}") from e


def task_extract_new(processed_path: Path, raw_paths: list[Path]):
    """Extract and classify new PDF files."""
    from papertrail.tasks.organization import rename_pdf_files

    lock_path = Path(__file__).parents[1].parent / "config" / ".extract.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.error("Another extract_new process is already running. Exiting.")
        lock_file.close()
        return
    try:
        _task_extract_new_locked(processed_path, raw_paths)
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def _task_extract_new_locked(processed_path: Path, raw_paths: list[Path]):
    """Extract and classify new PDF files (lock already held)."""
    from papertrail.tasks.organization import rename_pdf_files

    with task_log_context(processed_path, "extract_new"):
        logs_dir = processed_path / "logs"
        failure_log_path = logs_dir / "classification_failures.log"
        failure_logger = setup_failure_logger(failure_log_path)
        logger.debug(f"Logging failures to: {failure_log_path}")

        doc_logger = DocumentLogger()
        run_start = _time.monotonic()

        logger.info("Building hash index from metadata files...")
        known_content_hashes_idx, known_file_hashes_idx = build_hash_index(processed_path)
        known_content_hashes = set(known_content_hashes_idx.keys())
        known_file_hashes = set(known_file_hashes_idx.keys())

        logger.info("Scanning for new PDFs...")
        pdf_paths = find_pdf_files(raw_paths)
        logger.info(f"Found {len(pdf_paths)} PDFs in raw directories")

        logger.info("Stage 1: Quick filtering using fast file hashes...")
        fast_hash_map = {pdf: hash_file_fast(pdf) for pdf in tqdm(pdf_paths, desc="Fast hashing")}
        potentially_new = [pdf for pdf in pdf_paths if fast_hash_map[pdf] not in known_file_hashes]

        already_processed = len(pdf_paths) - len(potentially_new)
        logger.info(f"  -> Skipped {already_processed} already-processed files")
        logger.info(f"  -> {len(potentially_new)} files need content-based hashing")

        if not potentially_new:
            logger.info("No new PDFs to process.")
            return

        logger.info(f"Stage 2: Content-based hashing for {len(potentially_new)} new files...")
        content_hash_map = {}

        for pdf in tqdm(potentially_new, desc="Content hashing"):
            try:
                content_hash = hash_file_content(pdf)
                content_hash_map[pdf] = content_hash
            except Exception as e:
                logger.error(f"Error hashing {pdf.name}: {e}")

        files_to_process = [pdf for pdf in potentially_new if content_hash_map.get(pdf) not in known_content_hashes]
        logger.info(f"Found {len(files_to_process)} truly new PDFs to process.")

        success_count = len(known_content_hashes)
        initial_count = success_count

        if files_to_process:
            rename_pdf_files(files_to_process, content_hash_map, known_content_hashes, known_file_hashes, processed_path,
                             failure_logger, doc_logger=doc_logger)

        new_processed = len(known_content_hashes) - initial_count
        failed = len(files_to_process) - new_processed
        elapsed = _time.monotonic() - run_start
        logger.info(f"=== SUMMARY: {len(files_to_process)} attempted, {new_processed} success, {failed} failed, {elapsed:.1f}s total ===")


def _collect_reextract_targets(processed_path: Path, all_unknown: bool = False,
                                filename: str = None, document_pattern: str = None) -> list[tuple]:
    """Collect files to re-extract based on targeting mode."""
    import fnmatch

    json_files = list(processed_path.rglob("*.json"))
    if not json_files:
        logger.info(f"No metadata files found in {processed_path}")
        return []

    targets = []

    if filename:
        target_pdf = processed_path / filename
        target_json = target_pdf.with_suffix(".json")
        if not target_json.exists():
            logger.error(f"Metadata file not found: {target_json}")
            return []
        if not target_pdf.exists():
            logger.error(f"PDF file not found: {target_pdf}")
            return []
        with open(target_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        targets.append((target_json, target_pdf, data))

    elif document_pattern:
        for metadata_path in tqdm(json_files, desc="Matching pattern"):
            pdf_path = metadata_path.with_suffix(".pdf")
            if not fnmatch.fnmatch(pdf_path.name, document_pattern):
                continue
            if not pdf_path.exists():
                logger.warning(f"PDF not found for {metadata_path.name}")
                continue
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                targets.append((metadata_path, pdf_path, data))
            except Exception as e:
                logger.warning(f"Skipping {metadata_path.name}: {e}")

    elif all_unknown:
        for metadata_path in tqdm(json_files, desc="Scanning for $UNKNOWN$"):
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                has_unknown = (
                    data.get("document_type") == "$UNKNOWN$"
                    or data.get("issuing_party") == "$UNKNOWN$"
                    or data.get("issue_date") == "$UNKNOWN$"
                )
                if has_unknown:
                    pdf_path = metadata_path.with_suffix(".pdf")
                    if pdf_path.exists():
                        targets.append((metadata_path, pdf_path, data))
                    else:
                        logger.warning(f"PDF not found for {metadata_path.name}")
            except Exception as e:
                logger.warning(f"Skipping {metadata_path.name}: {e}")

    return targets


def task_reextract(processed_path: Path, dry_run: bool = False,
                   all_unknown: bool = False, filename: str = None, document_pattern: str = None):
    """Re-extract documents by re-running the full classification pipeline."""
    with task_log_context(processed_path, "reextract"):
        doc_logger = DocumentLogger()

        targets = _collect_reextract_targets(processed_path, all_unknown=all_unknown,
                                              filename=filename, document_pattern=document_pattern)
        if not targets:
            logger.info("No files to re-extract.")
            return

        logger.info(f"Found {len(targets)} files to re-extract")

        def classify_one(item):
            metadata_path, pdf_path, old_data = item
            try:
                content_hash = old_data.get("content_hash") or old_data.get("hash")
                if not content_hash:
                    return (metadata_path, old_data, None, "No content_hash in metadata")
                new_metadata = classify_pdf_document(pdf_path, content_hash, doc_logger=doc_logger)
                new_metadata.file_hash = old_data.get("file_hash") or old_data.get("_old_hash")
                new_metadata.create_date = old_data.get("create_date")
                new_metadata.update_date = datetime.now().strftime("%Y-%m-%d")
                return (metadata_path, old_data, new_metadata, None)
            except Exception as e:
                return (metadata_path, old_data, None, str(e))

        logger.info("Running re-extraction...")
        fixed_count = 0
        still_unknown_count = 0
        failed_count = 0

        for item in tqdm(targets, desc="Re-extracting"):
            metadata_path, old_data, new_metadata, error = classify_one(item)

            if error:
                logger.error(f"Failed {metadata_path.name}: {error}")
                failed_count += 1
                continue

            new_doc_type = new_metadata.document_type.value if hasattr(new_metadata.document_type, 'value') else new_metadata.document_type
            new_issuer = new_metadata.issuing_party.value if hasattr(new_metadata.issuing_party, 'value') else new_metadata.issuing_party
            new_date = new_metadata.issue_date

            changes = []
            old_doc_type = old_data.get("document_type", "")
            old_issuer = old_data.get("issuing_party", "")
            old_date = old_data.get("issue_date", "")

            if old_doc_type != new_doc_type:
                changes.append(f"document_type: {old_doc_type} -> {new_doc_type}")
            if old_issuer != new_issuer:
                changes.append(f"issuing_party: {old_issuer} -> {new_issuer}")
            if old_date != new_date:
                changes.append(f"issue_date: {old_date} -> {new_date}")

            if changes:
                logger.info(f"Changed {metadata_path.name}: {', '.join(changes)}")
                fixed_count += 1
            else:
                logger.debug(f"No changes: {metadata_path.name}")
                still_unknown_count += 1

            if not dry_run:
                save_metadata_json(metadata_path.with_suffix(".pdf"), new_metadata)

        logger.info("=" * 40)
        logger.info(f"Changed: {fixed_count}")
        logger.info(f"Unchanged: {still_unknown_count}")
        logger.info(f"Failed: {failed_count}")
        if dry_run:
            logger.info("(dry run - no files were modified)")
        else:
            if fixed_count > 0:
                logger.info("Run 'rename_files' task to update filenames based on new metadata.")


def task_validate_extraction(processed_path: Path, document_pattern: str = None):
    """Validate extraction quality by loading and inspecting metadata."""
    import fnmatch

    with task_log_context(processed_path, "validate_extraction"):
        json_files = list(processed_path.rglob("*.json"))
        if not json_files:
            logger.info(f"No metadata files found in {processed_path}")
            return

        issues_count = 0
        files_checked = 0

        for metadata_path in tqdm(json_files, desc="Validating extractions"):
            pdf_path = metadata_path.with_suffix(".pdf")

            if document_pattern and not fnmatch.fnmatch(pdf_path.name, document_pattern):
                continue

            files_checked += 1
            try:
                with open(metadata_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read {metadata_path.name}: {e}")
                issues_count += 1
                continue

            file_issues = []

            for field in ("document_type", "issuing_party", "issue_date"):
                if data.get(field) == "$UNKNOWN$":
                    file_issues.append(f"{field}=$UNKNOWN$")

            confidence = data.get("confidence")
            if confidence is not None and confidence < 0.7:
                file_issues.append(f"low_confidence={confidence}")

            for field in ("content_hash", "file_hash", "issue_date", "document_type", "issuing_party"):
                if not data.get(field):
                    file_issues.append(f"missing_{field}")

            if file_issues:
                issues_count += 1
                logger.warning(f"[ISSUE] {pdf_path.name}: {', '.join(file_issues)}")

            logger.debug(f"[METADATA] {pdf_path.name}: " + " ".join(
                f"{k}={v}" for k, v in data.items() if k != "reasoning"
            ))

        logger.info(f"=== SUMMARY: {files_checked} checked, {issues_count} with issues ===")
