"""Extraction and classification tasks."""

import fcntl
import json
import shutil
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from papertrail.console import get_console
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
    build_extraction_tools,
    get_qr_exclusions,
)
from papertrail.pdf import render_pdf_to_images, find_pdf_files, get_page_count
from papertrail.metadata import build_hash_index, save_metadata_json
from papertrail.hashing import hash_file_fast, hash_file_content

def enum_value(v):
    """Extract value from enum or return as-is."""
    return v.value if hasattr(v, 'value') else v
from papertrail.tasks import task_log_context
from papertrail.qr import extract_metadata_from_qr, QRExtractedMetadata
from papertrail.nif_lookup import NIFLookupCache

logger = get_logger('cli')


def _merge_qr_metadata(
    qr_metadata: QRExtractedMetadata,
    issue_date: str,
    document_type: str,
    total_amount: float | None,
    total_amount_currency: str | None,
    issuer_tax_number: str | None,
    locale: str | None,
    doc_logger: DocumentLogger | None,
) -> tuple[str, str, float | None, str | None, str | None, str | None]:
    """
    Merge QR-extracted metadata with LLM-extracted values.

    QR values override LLM values when present (100% confidence).

    Returns:
        Tuple of (issue_date, document_type, total_amount, total_amount_currency, issuer_tax_number, locale)
    """
    final_date = issue_date
    final_doc_type = document_type
    final_amount = total_amount
    final_currency = total_amount_currency
    final_tax_number = issuer_tax_number
    final_locale = locale

    if qr_metadata.issue_date:
        if doc_logger and final_date != qr_metadata.issue_date:
            doc_logger.log_qr_merge("issue_date", qr_metadata.issue_date, final_date)
        final_date = qr_metadata.issue_date

    if qr_metadata.document_type:
        if doc_logger and final_doc_type != qr_metadata.document_type:
            doc_logger.log_qr_merge("document_type", qr_metadata.document_type, final_doc_type)
        final_doc_type = qr_metadata.document_type

    if qr_metadata.total_amount is not None:
        if doc_logger and final_amount != qr_metadata.total_amount:
            doc_logger.log_qr_merge("total_amount", qr_metadata.total_amount, final_amount)
        final_amount = qr_metadata.total_amount

    if qr_metadata.total_amount_currency:
        if doc_logger and final_currency != qr_metadata.total_amount_currency:
            doc_logger.log_qr_merge("total_amount_currency", qr_metadata.total_amount_currency, final_currency)
        final_currency = qr_metadata.total_amount_currency

    if qr_metadata.issuer_tax_number:
        if doc_logger and final_tax_number != qr_metadata.issuer_tax_number:
            doc_logger.log_qr_merge("issuer_tax_number", qr_metadata.issuer_tax_number, final_tax_number)
        final_tax_number = qr_metadata.issuer_tax_number

    if qr_metadata.locale:
        if doc_logger and final_locale != qr_metadata.locale:
            doc_logger.log_qr_merge("locale", qr_metadata.locale, final_locale)
        final_locale = qr_metadata.locale

    return final_date, final_doc_type, final_amount, final_currency, final_tax_number, final_locale


def classify_pdf_document(pdf_path: Path, file_hash: str, failure_logger=None,
                          doc_logger: DocumentLogger = None) -> DocumentMetadata:
    """
    Classify a PDF document using QR extraction and LLM.

    Pipeline:
    1. Phase 0: QR extraction (fast, 100% accurate when found)
    2. Phase 1: LLM raw extraction (vision model)
    3. Phase 2: Normalization (map raw values to canonicals)
    4. Merge: QR values override LLM values
    """
    import main
    ctx = main.get_ctx()

    if doc_logger:
        doc_logger.start_document(pdf_path)

    # Phase 0: QR extraction (fast)
    qr_metadata = None
    qr_raw_data = None
    try:
        t0 = _time.monotonic()
        qr_metadata, qr_raw_data = extract_metadata_from_qr(pdf_path)
        if doc_logger:
            doc_logger.log_timing("qr_extraction", _time.monotonic() - t0)
            if qr_metadata:
                doc_logger.log_qr_extraction(
                    qr_metadata.extraction_source,
                    {
                        "issue_date": qr_metadata.issue_date,
                        "document_type": qr_metadata.document_type,
                        "total_amount": qr_metadata.total_amount,
                        "issuer_nif": qr_metadata.issuer_nif,
                        "issuer_tax_number": qr_metadata.issuer_tax_number,
                        "atcud": qr_metadata.atcud,
                        "locale": qr_metadata.locale,
                    },
                )
            else:
                doc_logger.log_qr_not_found()
    except Exception as e:
        logger.debug(f"QR extraction failed (continuing with LLM): {e}")
        if doc_logger:
            doc_logger.log_qr_not_found()

    # Determine fields to exclude based on QR results
    exclude_fields: set[str] = set()
    pre_extracted: dict = {}
    if qr_metadata:
        exclude_fields, pre_extracted = get_qr_exclusions(qr_metadata)
        if doc_logger and exclude_fields:
            doc_logger.log_qr_skip(exclude_fields)

    # Phase 1: Render PDF for LLM
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
            {"role": "system", "content": get_system_prompt_raw_extraction(pre_extracted if pre_extracted else None)},
            {"role": "user", "content": user_content},
        ]

        # Use dynamic tools - exclude QR-extracted fields to reduce tokens
        tools = build_extraction_tools(exclude_fields) if exclude_fields else TOOLS_RAW_EXTRACTION

        t0 = _time.monotonic()
        response = ctx.openai_client.chat.completions.create(
            model=ctx.model_id,
            max_tokens=4096,
            temperature=0,
            messages=messages,
            tools=tools,
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

        # Inject QR-extracted values into JSON before Pydantic parsing
        # These fields were excluded from LLM schema to save tokens, but are
        # required by DocumentMetadataRaw - must be present before validation
        if pre_extracted:
            args_dict = json.loads(args)
            for field, value in pre_extracted.items():
                if field not in args_dict:
                    args_dict[field] = value
            args = json.dumps(args_dict)

        raw_metadata = DocumentMetadataRaw.model_validate_json(args)

        if doc_logger:
            doc_logger.log_extraction(raw_metadata.model_dump())

        # Phase 2: Normalization
        t0 = _time.monotonic()
        normalized_doc_type, normalized_issuing_party = normalize_metadata(
            raw_metadata, ctx.openai_client, ctx.model_id, mappings=ctx.mappings_manager,
            doc_logger=doc_logger,
        )
        if doc_logger:
            doc_logger.log_timing("normalization", _time.monotonic() - t0)

        # Start with LLM values
        final_issue_date = raw_metadata.issue_date
        final_doc_type = normalized_doc_type
        final_amount = raw_metadata.total_amount
        final_currency = raw_metadata.total_amount_currency
        final_tax_number = raw_metadata.issuer_tax_number
        final_locale = raw_metadata.locale

        # Merge: QR values override LLM values
        if qr_metadata:
            final_issue_date, final_doc_type, final_amount, final_currency, final_tax_number, final_locale = _merge_qr_metadata(
                qr_metadata,
                final_issue_date,
                final_doc_type,
                final_amount,
                final_currency,
                final_tax_number,
                final_locale,
                doc_logger,
            )

        # Phase 4: NIF Enrichment (only for valid Portuguese tax numbers with Portuguese locale)
        if final_tax_number and ctx.nif_cache and final_locale == "pt-PT" and NIFLookupCache.is_portuguese_nif(final_tax_number):
            t0 = _time.monotonic()
            official_issuer, lookup_source, lookup_error = ctx.nif_cache.lookup(final_tax_number)

            if official_issuer:
                # Log the lookup result
                if doc_logger:
                    if lookup_source == "cache":
                        doc_logger.log_nif_cache_hit(final_tax_number, official_issuer)
                    elif lookup_source == "web":
                        doc_logger.log_nif_web_lookup(final_tax_number, official_issuer)

                # Re-normalize the official name to canonical form
                # Create a minimal raw metadata object for normalization
                nif_raw = DocumentMetadataRaw(
                    issue_date=final_issue_date,
                    document_type=raw_metadata.document_type,
                    issuing_party=official_issuer,
                    service_name=raw_metadata.service_name,
                    total_amount=final_amount,
                    total_amount_currency=final_currency,
                    confidence=1.0,
                    reasoning="NIF lookup override",
                )
                _, nif_normalized_issuer = normalize_metadata(
                    nif_raw, ctx.openai_client, ctx.model_id, mappings=ctx.mappings_manager,
                )

                # Only use the NIF-derived issuer if normalization succeeded
                if nif_normalized_issuer != "$UNKNOWN$":
                    if doc_logger:
                        doc_logger.log_nif_enrichment(final_tax_number, official_issuer, nif_normalized_issuer)
                    normalized_issuing_party = nif_normalized_issuer
                else:
                    logger.debug(f"[NIF-ENRICH] Keeping original issuer (NIF name didn't normalize): {official_issuer}")
            elif doc_logger:
                if lookup_source == "web_error" and lookup_error:
                    doc_logger.log_nif_web_error(final_tax_number, lookup_error)
                else:
                    doc_logger.log_nif_not_found(final_tax_number, lookup_source)

            if doc_logger:
                doc_logger.log_timing("nif_enrichment", _time.monotonic() - t0)

            # Save cache after enrichment
            ctx.nif_cache.save()

        metadata = DocumentMetadata(
            issue_date=final_issue_date,
            document_type=final_doc_type,
            issuing_party=normalized_issuing_party,
            service_name=raw_metadata.service_name,
            total_amount=final_amount,
            total_amount_currency=final_currency,
            confidence=1.0 if qr_metadata else raw_metadata.confidence,
            reasoning=raw_metadata.reasoning,
            content_hash=file_hash,
            document_type_raw=raw_metadata.document_type,
            issuing_party_raw=raw_metadata.issuing_party,
            issuer_tax_number=final_tax_number,
            locale=final_locale,
            qrcode=qr_raw_data,
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

    console = get_console()

    with task_log_context(processed_path, "extract_new"):
        logs_dir = processed_path / "logs"
        failure_log_path = logs_dir / "classification_failures.log"
        failure_logger = setup_failure_logger(failure_log_path)
        logger.debug(f"Logging failures to: {failure_log_path}")

        doc_logger = DocumentLogger()
        run_start = _time.monotonic()

        logger.debug("Building hash index from metadata files...")
        known_content_hashes_idx, known_file_hashes_idx = build_hash_index(processed_path)
        known_content_hashes = set(known_content_hashes_idx.keys())
        known_file_hashes = set(known_file_hashes_idx.keys())

        logger.debug("Scanning for new PDFs...")
        pdf_paths = find_pdf_files(raw_paths)
        logger.debug(f"Found {len(pdf_paths)} PDFs in raw directories")

        logger.debug("Stage 1: Quick filtering using fast file hashes...")

        # Stage 1: Fast hashing with Rich progress
        fast_hash_map = {}
        with console.progress("Fast hashing", total=len(pdf_paths)) as progress:
            task = progress.add_task("Fast hashing", total=len(pdf_paths))
            for pdf in pdf_paths:
                fast_hash_map[pdf] = hash_file_fast(pdf)
                progress.update(task, advance=1)

        potentially_new = [pdf for pdf in pdf_paths if fast_hash_map[pdf] not in known_file_hashes]

        already_processed = len(pdf_paths) - len(potentially_new)
        logger.debug(f"Skipped {already_processed} already-processed files")
        logger.debug(f"{len(potentially_new)} files need content-based hashing")

        if not potentially_new:
            console.success(f"{len(pdf_paths)} PDFs scanned, 0 new to process", indent=False)
            logger.debug("No new PDFs to process.")
            return

        logger.debug(f"Stage 2: Content-based hashing for {len(potentially_new)} new files...")
        content_hash_map = {}

        # Stage 2: Content hashing with Rich progress
        with console.progress("Content hashing", total=len(potentially_new)) as progress:
            task = progress.add_task("Content hashing", total=len(potentially_new))
            for pdf in potentially_new:
                try:
                    content_hash = hash_file_content(pdf)
                    content_hash_map[pdf] = content_hash
                except Exception as e:
                    logger.error(f"Error hashing {pdf.name}: {e}")
                progress.update(task, advance=1)

        files_to_process = [pdf for pdf in potentially_new if content_hash_map.get(pdf) not in known_content_hashes]
        logger.debug(f"Found {len(files_to_process)} truly new PDFs to process.")

        success_count = len(known_content_hashes)
        initial_count = success_count

        if files_to_process:
            rename_pdf_files(files_to_process, content_hash_map, known_content_hashes, known_file_hashes, processed_path,
                             failure_logger, doc_logger=doc_logger)

        new_processed = len(known_content_hashes) - initial_count
        failed = len(files_to_process) - new_processed
        elapsed = _time.monotonic() - run_start

        # Console output
        console.success(f"{len(pdf_paths)} PDFs scanned, {len(files_to_process)} new to process", indent=False)
        if new_processed > 0 or failed > 0:
            if failed > 0:
                console.warning(f"{new_processed} processed, {failed} failed", indent=False)
            else:
                console.success(f"{new_processed} processed successfully", indent=False)

        logger.debug(f"=== SUMMARY: {len(files_to_process)} attempted, {new_processed} success, {failed} failed, {elapsed:.1f}s total ===")


def _collect_sync_targets(processed_path: Path, all_unknown: bool = False,
                          pattern: str = None, orphans_only: bool = False) -> list[tuple]:
    """Collect files to sync based on targeting mode.

    Args:
        processed_path: Path to the processed documents directory.
        all_unknown: If True, collect files with $UNKNOWN$ values (requires existing JSON).
        pattern: Unified pattern (glob or regex, auto-detected). Supports:
            - Exact filename: "2025-01-01 - invoice.pdf"
            - Glob pattern: "*anthropic*.pdf"
            - Regex pattern: "2025-01-\\d{2}"
        orphans_only: If True, only collect PDFs without existing metadata JSON.
                      This is the default when no other flags are specified.

    Returns:
        List of tuples: (metadata_path, pdf_path, old_data_or_None)
        old_data is None for PDFs without existing metadata JSON.
    """
    from papertrail.pattern_utils import make_matcher

    console = get_console()

    # Scan for PDFs, not JSONs - allows processing files without existing metadata
    pdf_files = list(processed_path.rglob("*.pdf"))
    if not pdf_files:
        logger.debug(f"No PDF files found in {processed_path}")
        return []

    targets = []

    if pattern:
        # Fast path: check if pattern is an exact filename
        target_pdf = processed_path / pattern
        if target_pdf.exists() and target_pdf.suffix.lower() == '.pdf':
            target_json = target_pdf.with_suffix(".json")
            data = None
            if target_json.exists():
                # Skip if orphans_only and has metadata
                if orphans_only:
                    return targets
                with open(target_json, "r", encoding="utf-8") as f:
                    data = json.load(f)
            targets.append((target_json, target_pdf, data))
        else:
            # Pattern matching (glob or regex)
            matcher = make_matcher(pattern)
            with console.progress("Matching pattern", total=len(pdf_files)) as progress:
                task = progress.add_task("Matching pattern", total=len(pdf_files))
                for pdf_path in pdf_files:
                    if not matcher(pdf_path.name):
                        progress.update(task, advance=1)
                        continue
                    metadata_path = pdf_path.with_suffix(".json")
                    has_metadata = metadata_path.exists()

                    # Skip if orphans_only and has metadata
                    if orphans_only and has_metadata:
                        progress.update(task, advance=1)
                        continue

                    data = None
                    if has_metadata:
                        try:
                            with open(metadata_path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                        except Exception as e:
                            logger.warning(f"Failed to load {metadata_path.name}: {e}")
                    targets.append((metadata_path, pdf_path, data))
                    progress.update(task, advance=1)

    elif orphans_only:
        # Collect only orphans (no pattern, no all_unknown)
        with console.progress("Scanning for orphans", total=len(pdf_files)) as progress:
            task = progress.add_task("Scanning for orphans", total=len(pdf_files))
            for pdf_path in pdf_files:
                metadata_path = pdf_path.with_suffix(".json")
                if not metadata_path.exists():
                    targets.append((metadata_path, pdf_path, None))
                progress.update(task, advance=1)

    if all_unknown:
        # all_unknown requires existing JSON files to check for $UNKNOWN$ values
        json_files = list(processed_path.rglob("*.json"))
        with console.progress("Scanning for $UNKNOWN$", total=len(json_files)) as progress:
            task = progress.add_task("Scanning for $UNKNOWN$", total=len(json_files))
            for metadata_path in json_files:
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
                progress.update(task, advance=1)

    # Sort by PDF filename descending (newest first, since filenames start with YYYY-MM-DD)
    targets.sort(key=lambda t: t[1].name, reverse=True)
    return targets


def task_sync(processed_path: Path, dry_run: bool = False,
              all_unknown: bool = False, pattern: str = None,
              workers: int = 1, all: bool = False):
    """Sync metadata for PDF documents by running the classification pipeline.

    Default behavior (no flags): only process PDFs without metadata (orphans).
    Use --all to process all matching PDFs, re-extracting existing metadata.

    Args:
        processed_path: Path to the processed documents directory.
        dry_run: If True, show what would be changed without modifying files.
        all_unknown: Re-extract all files with $UNKNOWN$ values.
        pattern: Unified pattern for matching files (glob or regex, auto-detected).
        workers: Number of parallel workers (default: 1 for sequential).
        all: If True, process all matching PDFs (not just orphans).
    """
    console = get_console()

    # Determine orphans_only mode
    # Default (no flags): orphans only
    # With --all, --all_unknown, or --pattern: process accordingly
    orphans_only = not all and not all_unknown and pattern is None

    with task_log_context(processed_path, "sync"):
        targets = _collect_sync_targets(processed_path, all_unknown=all_unknown,
                                        pattern=pattern, orphans_only=orphans_only)
        if not targets:
            console.warning("No files to sync", indent=False)
            return

        logger.debug(f"Found {len(targets)} files to sync (workers={workers})")

        def classify_one(item):
            """Classify a single document (thread-safe)."""
            metadata_path, pdf_path, old_data = item
            # Each thread gets its own DocumentLogger instance
            thread_doc_logger = DocumentLogger()
            try:
                if old_data is None:
                    # Fresh extraction - compute hashes
                    content_hash = hash_file_content(pdf_path)
                    file_hash = hash_file_fast(pdf_path)
                    create_date = datetime.now().strftime("%Y-%m-%d")
                else:
                    content_hash = old_data.get("content_hash") or old_data.get("hash")
                    if not content_hash:
                        return (metadata_path, old_data, None, "No content_hash in metadata")
                    file_hash = old_data.get("file_hash") or old_data.get("_old_hash")
                    create_date = old_data.get("create_date")

                new_metadata = classify_pdf_document(pdf_path, content_hash, doc_logger=thread_doc_logger)
                new_metadata.file_hash = file_hash
                new_metadata.create_date = create_date
                new_metadata.update_date = datetime.now().strftime("%Y-%m-%d")
                return (metadata_path, old_data, new_metadata, None)
            except Exception as e:
                return (metadata_path, old_data, None, str(e))

        logger.debug("Running sync...")

        # Counters for summary statistics
        fixed_count = 0
        still_unknown_count = 0
        failed_count = 0
        new_count = 0
        renamed_count = 0

        def process_result(metadata_path, old_data, new_metadata, error):
            """Process a single extraction result and save JSON immediately."""
            nonlocal fixed_count, still_unknown_count, failed_count, new_count, renamed_count

            if error:
                logger.error(f"Failed {metadata_path.name}: {error}")
                failed_count += 1
                return

            new_doc_type = enum_value(new_metadata.document_type)
            new_issuer = enum_value(new_metadata.issuing_party)
            new_date = new_metadata.issue_date

            if old_data is None:
                # Fresh extraction - no comparison possible
                logger.debug(f"New extraction: {metadata_path.name} -> {new_doc_type}, {new_issuer}, {new_date}")
                new_count += 1
            else:
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
                    logger.debug(f"Changed {metadata_path.name}: {', '.join(changes)}")
                    fixed_count += 1
                else:
                    logger.debug(f"No changes: {metadata_path.name}")
                    still_unknown_count += 1

            # Save JSON immediately after each successful extraction
            if not dry_run:
                save_metadata_json(metadata_path.with_suffix(".pdf"), new_metadata)

                # Rename PDF+JSON pair if filename changed
                from papertrail.tasks.organization import file_name_from_metadata
                new_filename = file_name_from_metadata(new_metadata, new_metadata.content_hash)
                new_pdf_path = metadata_path.parent / new_filename
                old_pdf_path = metadata_path.with_suffix(".pdf")
                if old_pdf_path != new_pdf_path:
                    new_json_path = new_pdf_path.with_suffix(".json")
                    shutil.move(str(old_pdf_path), str(new_pdf_path))
                    shutil.move(str(metadata_path), str(new_json_path))
                    logger.debug(f"Renamed: {old_pdf_path.name} -> {new_pdf_path.name}")
                    renamed_count += 1

        if workers == 1:
            # Sequential path (backwards compatible)
            with console.progress("Syncing", total=len(targets)) as progress:
                task = progress.add_task("Syncing", total=len(targets))
                for item in targets:
                    metadata_path, old_data, new_metadata, error = classify_one(item)
                    process_result(metadata_path, old_data, new_metadata, error)
                    progress.update(task, advance=1)
        else:
            # Parallel path - process results as they complete
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(classify_one, item): item for item in targets}
                with console.progress("Syncing", total=len(futures)) as progress:
                    task = progress.add_task("Syncing", total=len(futures))
                    for future in as_completed(futures):
                        metadata_path, old_data, new_metadata, error = future.result()
                        process_result(metadata_path, old_data, new_metadata, error)
                        progress.update(task, advance=1)

        # Summary output
        parts = []
        if new_count > 0:
            parts.append(f"{new_count} new")
        if fixed_count > 0:
            parts.append(f"{fixed_count} changed")
        if renamed_count > 0:
            parts.append(f"{renamed_count} renamed")
        if still_unknown_count > 0:
            parts.append(f"{still_unknown_count} unchanged")
        if failed_count > 0:
            parts.append(f"{failed_count} failed")

        summary = ", ".join(parts) if parts else "No files processed"

        if failed_count > 0:
            console.warning(summary, indent=False)
        elif new_count > 0 or fixed_count > 0:
            console.success(summary, indent=False)
        else:
            console.info(f"No changes ({still_unknown_count} files checked)", indent=False)

        logger.debug(f"New: {new_count}, Changed: {fixed_count}, Renamed: {renamed_count}, Unchanged: {still_unknown_count}, Failed: {failed_count}")

        if dry_run:
            console.detail("(dry run - no files were modified)", indent=False)


def task_validate_extraction(processed_path: Path, pattern: str = None):
    """Validate extraction quality by loading and inspecting metadata.

    Args:
        processed_path: Path to the processed documents directory.
        pattern: Unified pattern for matching files (glob or regex, auto-detected).
                 If None, validates all files.
    """
    from papertrail.pattern_utils import make_matcher

    console = get_console()

    with task_log_context(processed_path, "validate_extraction"):
        json_files = list(processed_path.rglob("*.json"))
        if not json_files:
            console.warning("No metadata files found", indent=False)
            return

        # Create matcher if pattern provided
        matcher = make_matcher(pattern) if pattern else None

        issues_count = 0
        files_checked = 0

        with console.progress("Validating extractions", total=len(json_files)) as progress:
            task = progress.add_task("Validating extractions", total=len(json_files))
            for metadata_path in json_files:
                pdf_path = metadata_path.with_suffix(".pdf")

                if matcher and not matcher(pdf_path.name):
                    progress.update(task, advance=1)
                    continue

                files_checked += 1
                try:
                    with open(metadata_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except Exception as e:
                    logger.warning(f"Failed to read {metadata_path.name}: {e}")
                    issues_count += 1
                    progress.update(task, advance=1)
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

                progress.update(task, advance=1)

        # Summary output
        if issues_count > 0:
            console.warning(f"{files_checked} checked, {issues_count} with issues", indent=False)
        else:
            console.success(f"{files_checked} checked, no issues found", indent=False)

        logger.debug(f"=== SUMMARY: {files_checked} checked, {issues_count} with issues ===")
