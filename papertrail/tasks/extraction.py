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
from papertrail.models import DocumentMetadata, DocumentMetadataRaw, SubDocumentMetadata
from papertrail.llm import (
    get_system_prompt_classify,
    normalize_issuing_party,
    build_extraction_tools,
    get_qr_exclusions,
)
from papertrail.models import (
    add_session_type,
    add_session_party,
)
from papertrail.pdf import render_pdf_to_images, find_pdf_files, find_document_files, get_page_count, is_image_file
from papertrail.metadata import build_hash_index, save_metadata_json, load_json_data, iter_json_files
from papertrail.hashing import hash_file_fast, hash_file_content, hash_file_text
from papertrail.hashing import dedup_batch

from papertrail.tasks import task_log_context
from papertrail.qr import extract_all_metadata_from_qr, QRExtractedMetadata
from papertrail.nif_lookup import NIFLookupCache

logger = get_logger('cli')


_QR_MERGE_FIELDS = (
    "issue_date", "document_type", "total_amount",
    "total_amount_currency", "issuer_tax_number", "locale",
)


def _merge_qr_metadata(
    qr_metadata: QRExtractedMetadata,
    llm_values: dict,
    doc_logger: DocumentLogger | None,
) -> dict:
    """Merge QR-extracted metadata with LLM-extracted values.

    QR values override LLM values when present (100% confidence).
    Mutates and returns llm_values dict.
    """
    for field in _QR_MERGE_FIELDS:
        qr_val = getattr(qr_metadata, field)
        if qr_val is not None:
            if doc_logger and llm_values[field] != qr_val:
                doc_logger.log_qr_merge(field, qr_val, llm_values[field])
            llm_values[field] = qr_val
    return llm_values


def _phase0_qr_extract(pdf_path, doc_logger):
    """Phase 0: Extract metadata from QR codes (fast, 100% accurate).

    Returns 3-tuple: (qr_metadata, qr_raw_data, all_qr_results)
    - 0 results: (None, None, [])
    - 1 result: (metadata, raw_data, []) — single-QR path
    - 2+ results: (None, None, all_results) — multi-QR, sub-documents
    """
    try:
        t0 = _time.monotonic()
        all_results = extract_all_metadata_from_qr(pdf_path)
        if doc_logger:
            doc_logger.log_timing("qr_extraction", _time.monotonic() - t0)

        if not all_results:
            if doc_logger:
                doc_logger.log_qr_not_found()
            return None, None, []

        if len(all_results) == 1:
            qr_metadata, qr_raw_data = all_results[0]
            if doc_logger:
                doc_logger.log_qr_extraction(
                    qr_metadata.extraction_source,
                    {k: getattr(qr_metadata, k) for k in (
                        "issue_date", "document_type", "total_amount",
                        "issuer_nif", "issuer_tax_number", "atcud", "locale",
                    )},
                )
            return qr_metadata, qr_raw_data, []

        # 2+ results: multi-QR mode
        if doc_logger:
            doc_logger.log_multi_qr(len(all_results), pdf_path.name)
            for qr_meta, qr_raw in all_results:
                doc_logger.log_qr_extraction(
                    qr_meta.extraction_source,
                    {k: getattr(qr_meta, k) for k in (
                        "issue_date", "document_type", "total_amount",
                        "issuer_nif", "issuer_tax_number", "atcud", "locale",
                    )},
                    page_number=qr_raw.get("page_number", 0) if qr_raw else 0,
                )
        return None, None, all_results

    except Exception as e:
        logger.debug(f"QR extraction failed (continuing with LLM): {e}")
        if doc_logger:
            doc_logger.log_qr_not_found()
        return None, None, []


def _phase1_llm_extract(pdf_path, exclude_fields, pre_extracted, ctx, doc_logger,
                        multi_qr_info=None):
    """Phase 1: Render PDF and extract metadata via LLM vision model."""
    t0 = _time.monotonic()
    images_b64 = render_pdf_to_images(pdf_path)
    if doc_logger:
        doc_logger.log_timing("pdf_render", _time.monotonic() - t0)

    user_content = [
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
        for img_b64 in images_b64
    ]
    messages = [
        {"role": "system", "content": get_system_prompt_classify(
            pre_extracted or None, multi_qr_info=multi_qr_info)},
        {"role": "user", "content": user_content},
    ]

    t0 = _time.monotonic()
    response = ctx.openai_client.chat.completions.create(
        model=ctx.model_id,
        max_tokens=4096,
        temperature=0,
        messages=messages,
        tools=build_extraction_tools(exclude_fields),
        tool_choice={"type": "function", "function": {"name": "extract_document_metadata"}},
    )
    if doc_logger:
        doc_logger.log_timing("llm_extraction", _time.monotonic() - t0)
        if response.usage:
            doc_logger.log_llm_usage(ctx.model_id, response.usage.prompt_tokens, response.usage.completion_tokens)

    # Warn if the API appears to have dropped images (token count too low for vision)
    if response.usage and response.usage.prompt_tokens < 100 and images_b64:
        logger.warning(
            f"[VISION-WARNING] Suspiciously low prompt_tokens={response.usage.prompt_tokens} "
            f"for vision request with {len(images_b64)} images ({pdf_path.name}). "
            f"The API may not be forwarding images — classification may be hallucinated."
        )

    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        raise ValueError("OpenRouter did not return structured classification.")

    args = tool_calls[0].function.arguments

    # Inject QR-extracted values before Pydantic parsing
    if pre_extracted:
        args_dict = json.loads(args)
        for field, value in pre_extracted.items():
            if field not in args_dict:
                args_dict[field] = value
        args = json.dumps(args_dict)

    raw_metadata = DocumentMetadataRaw.model_validate_json(args)
    if doc_logger:
        doc_logger.log_extraction(raw_metadata.model_dump())
    return raw_metadata


def _enrich_nif(tax_number, ctx, doc_logger) -> tuple[str | None, str | None]:
    """Shared NIF enrichment. Returns (normalized_party, raw_party) or (None, None)."""
    if not (tax_number and ctx.nif_cache and NIFLookupCache.is_portuguese_nif(tax_number)):
        return None, None

    official_issuer, lookup_source, lookup_error = ctx.nif_cache.lookup(tax_number)

    if not official_issuer:
        if doc_logger:
            if lookup_source == "web_error" and lookup_error:
                doc_logger.log_nif_web_error(tax_number, lookup_error)
            else:
                doc_logger.log_nif_not_found(tax_number, lookup_source)
        return None, None

    if doc_logger:
        if lookup_source == "cache":
            doc_logger.log_nif_cache_hit(tax_number, official_issuer)
        elif lookup_source == "web":
            doc_logger.log_nif_web_lookup(tax_number, official_issuer)

    cached_normalized = ctx.nif_cache.get_normalized(tax_number)
    if cached_normalized:
        if doc_logger:
            doc_logger.log_nif_enrichment(tax_number, official_issuer, cached_normalized)
        return cached_normalized, official_issuer

    nif_normalized = normalize_issuing_party(official_issuer, ctx.openai_client, ctx.model_id)
    if nif_normalized != "$UNKNOWN$":
        ctx.nif_cache.set_normalized(tax_number, nif_normalized)
        if doc_logger:
            doc_logger.log_nif_enrichment(tax_number, official_issuer, nif_normalized)
        return nif_normalized, official_issuer

    logger.debug(f"[NIF-ENRICH] Keeping original issuer (NIF name didn't normalize): {official_issuer}")
    return None, official_issuer


def _phase4_nif_enrich(merged, raw_metadata, normalized_issuing_party, ctx, doc_logger):
    """Phase 4: Enrich issuing party using NIF tax number lookup."""
    if merged["locale"] != "pt-PT":
        return normalized_issuing_party

    t0 = _time.monotonic()
    enriched, _ = _enrich_nif(merged["issuer_tax_number"], ctx, doc_logger)
    if enriched:
        normalized_issuing_party = enriched
    if doc_logger:
        doc_logger.log_timing("nif_enrichment", _time.monotonic() - t0)
    ctx.nif_cache.save()
    return normalized_issuing_party


def _build_sub_documents(all_qr_results, ctx, doc_logger):
    """Build sub-document metadata list from multiple QR results."""
    sub_docs = []
    for qr_metadata, qr_raw_data in all_qr_results:
        enriched, raw_issuer = _enrich_nif(qr_metadata.issuer_tax_number, ctx, doc_logger)
        if enriched:
            add_session_party(enriched)

        sub_doc = SubDocumentMetadata(
            date_issued=qr_metadata.issue_date,
            document_type=qr_metadata.document_type,
            total_amount=qr_metadata.total_amount,
            total_amount_currency=qr_metadata.total_amount_currency,
            issuer_tax_number=qr_metadata.issuer_tax_number,
            issuing_party=enriched,
            issuing_party_raw=raw_issuer,
            document_number=qr_metadata.document_number,
            atcud=qr_metadata.atcud,
            locale=qr_metadata.locale,
            qrcode=qr_raw_data,
        )
        sub_docs.append(sub_doc.model_dump())

    if ctx.nif_cache:
        ctx.nif_cache.save()

    return sub_docs


def classify_pdf_document(pdf_path: Path, file_hash: str, failure_logger=None,
                          doc_logger: DocumentLogger = None) -> DocumentMetadata:
    """Classify a PDF document using QR extraction and LLM.

    Pipeline: QR extract -> LLM classify (single call) -> Merge QR overrides -> NIF enrich
    """
    from papertrail.config import get_ctx
    ctx = get_ctx()

    if doc_logger:
        doc_logger.start_document(pdf_path)

    # Phase 0: QR extraction
    qr_metadata, qr_raw_data, all_qr_results = _phase0_qr_extract(pdf_path, doc_logger)
    is_multi_qr = len(all_qr_results) >= 2

    exclude_fields, pre_extracted = set(), {}
    if qr_metadata and not is_multi_qr:
        exclude_fields, pre_extracted = get_qr_exclusions(qr_metadata)
        if doc_logger and exclude_fields:
            doc_logger.log_qr_skip(exclude_fields)

    # Build multi-QR context for LLM prompt
    multi_qr_info = None
    if is_multi_qr:
        issuers = []
        for qr_meta, _ in all_qr_results:
            name = qr_meta.issuer_nif or qr_meta.issuer_tax_number
            if name and name not in issuers:
                issuers.append(name)
        multi_qr_info = {"count": len(all_qr_results), "issuers": issuers}
        if doc_logger:
            logger.debug(f"[MULTI-QR] LLM context: {multi_qr_info}")

    try:
        # Classify: single LLM call (extract + normalize) with retry
        raw_metadata = None
        for attempt in range(2):
            try:
                raw_metadata = _phase1_llm_extract(pdf_path, exclude_fields, pre_extracted, ctx, doc_logger,
                                                   multi_qr_info=multi_qr_info)
                break
            except Exception as e:
                if attempt == 0:
                    logger.warning(f"LLM classification attempt 1 failed for {pdf_path.name}, retrying: {e}")
                else:
                    logger.warning(f"LLM classification attempt 2 failed for {pdf_path.name}, using fallback: {e}")
                    raw_metadata = DocumentMetadataRaw(
                        issue_date="$UNKNOWN$",
                        document_type="$UNKNOWN$",
                        document_type_raw="$UNKNOWN$",
                        issuing_party="$UNKNOWN$",
                        issuing_party_raw="$UNKNOWN$",
                        confidence=0.0,
                        reasoning="LLM classification failed after 2 attempts",
                    )

        # Build sub-documents for multi-QR PDFs (before merge, needs ctx for NIF)
        sub_documents = _build_sub_documents(all_qr_results, ctx, doc_logger) if is_multi_qr else None

        # Merge QR overrides (skip for multi-QR — QR data lives in sub_documents)
        merged = {
            "issue_date": raw_metadata.issue_date,
            "document_type": raw_metadata.document_type,
            "total_amount": raw_metadata.total_amount,
            "total_amount_currency": raw_metadata.total_amount_currency,
            "issuer_tax_number": raw_metadata.issuer_tax_number,
            "locale": raw_metadata.locale,
        }
        if qr_metadata and not is_multi_qr:
            _merge_qr_metadata(qr_metadata, merged, doc_logger)

        # Track new types/parties for session cache
        if merged["document_type"] != "$UNKNOWN$":
            add_session_type(merged["document_type"])

        # NIF enrichment
        normalized_issuing_party = raw_metadata.issuing_party
        normalized_issuing_party = _phase4_nif_enrich(
            merged, raw_metadata, normalized_issuing_party, ctx, doc_logger,
        )

        if normalized_issuing_party != "$UNKNOWN$":
            add_session_party(normalized_issuing_party)

        metadata = DocumentMetadata(
            date_issued=merged["issue_date"],
            document_type=merged["document_type"],
            issuing_party=normalized_issuing_party,
            total_amount=merged["total_amount"],
            total_amount_currency=merged["total_amount_currency"],
            class_confidence=raw_metadata.confidence if is_multi_qr else (1.0 if qr_metadata else raw_metadata.confidence),
            class_reasoning=raw_metadata.reasoning,
            hash_content=file_hash,
            document_type_raw=raw_metadata.document_type_raw,
            document_title=raw_metadata.document_title,
            issuing_party_raw=raw_metadata.issuing_party_raw,
            issuer_tax_number=merged["issuer_tax_number"],
            locale=merged["locale"],
            qrcode=None if is_multi_qr else qr_raw_data,
            sub_documents=sub_documents,
        )

        now = datetime.now().strftime("%Y-%m-%d")
        metadata.date_created = now
        metadata.date_updated = now
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


def task_extract_new(processed_path: Path, raw_paths: list[Path], quiet: bool = False) -> dict | None:
    """Extract and classify new PDF files."""
    lock_path = Path(__file__).parents[1].parent / ".cache" / ".extract.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.error("Another extract_new process is already running. Exiting.")
        lock_file.close()
        return None
    try:
        return _task_extract_new_locked(processed_path, raw_paths, quiet=quiet)
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def _process_xlsx_files(xlsx_paths: list[Path], known_file_hashes: set,
                        known_content_hashes: set, processed_path: Path,
                        console) -> tuple[int, int]:
    """Process XLSX files (bank statements). Returns (processed, skipped)."""
    import warnings
    from papertrail.bank_statement import classify_bank_statement
    from papertrail.tasks.organize import file_name_from_metadata

    processed_count = 0
    skipped_count = 0
    warnings.filterwarnings("ignore", message="Workbook contains no default style")

    for xlsx_path in console.track(xlsx_paths, "Processing XLSX"):
        file_hash = hash_file_fast(xlsx_path)
        if file_hash in known_file_hashes:
            skipped_count += 1
            continue

        metadata = classify_bank_statement(xlsx_path, file_hash)
        if metadata is not None:
            metadata.file_size_kb = round(xlsx_path.stat().st_size / 1024)
        if metadata is None:
            logger.debug(f"Skipping unrecognized XLSX: {xlsx_path.name}")
            skipped_count += 1
            continue

        filename = file_name_from_metadata(metadata, file_hash)
        new_path = processed_path / filename
        if new_path.exists():
            logger.warning(f"Skipping {xlsx_path.name}: destination already exists: {filename}")
            skipped_count += 1
            continue

        shutil.copy2(xlsx_path, new_path)
        save_metadata_json(new_path, metadata)

        known_file_hashes.add(file_hash)
        known_content_hashes.add(file_hash)
        processed_count += 1
        logger.debug(f"Processed XLSX: {xlsx_path.name} -> {filename}")

    return processed_count, skipped_count


def _task_extract_new_locked(processed_path: Path, raw_paths: list[Path], quiet: bool = False) -> dict:
    """Extract and classify new PDF files (lock already held)."""
    from papertrail.tasks.organize import rename_pdf_files

    console = get_console()

    with task_log_context(processed_path, "extract_new", show_header=not quiet):
        logs_dir = processed_path / "logs"
        failure_log_path = logs_dir / "classification_failures.log"
        failure_logger = setup_failure_logger(failure_log_path)
        logger.debug(f"Logging failures to: {failure_log_path}")

        doc_logger = DocumentLogger()
        run_start = _time.monotonic()

        logger.debug("Building hash index from metadata files...")
        known_content_hashes_idx, known_file_hashes_idx, known_text_hashes_idx, known_issuers_before = build_hash_index(processed_path)
        known_content_hashes = set(known_content_hashes_idx.keys())
        known_file_hashes = set(known_file_hashes_idx.keys())
        known_text_hashes = set(known_text_hashes_idx.keys())
        hashes_before = set(known_content_hashes)

        # Discover all document files (PDF + XLSX + images)
        all_doc_paths = find_document_files(raw_paths)
        pdf_paths = [p for p in all_doc_paths if p.suffix.lower() == '.pdf']
        xlsx_paths = [p for p in all_doc_paths if p.suffix.lower() == '.xlsx']
        image_paths = [p for p in all_doc_paths if is_image_file(p)]

        logger.debug(f"Found {len(pdf_paths)} PDFs, {len(xlsx_paths)} XLSX, and {len(image_paths)} images in raw directories")

        # Convert images to PDFs in a temp directory
        temp_dir = None
        images_converted = 0
        if image_paths:
            import tempfile
            from papertrail.pdf import convert_images_to_pdfs
            temp_dir = tempfile.TemporaryDirectory()
            converted_pdfs = convert_images_to_pdfs(image_paths, Path(temp_dir.name), console)
            images_converted = len(converted_pdfs)
            pdf_paths.extend(converted_pdfs)
            if images_converted > 0:
                logger.debug(f"Converted {images_converted} images to PDF")

        # Split multi-note PDF bundles into individual pages
        split_temp_dir = None
        bundles_split = 0
        split_pages_count = 0
        if pdf_paths:
            import tempfile
            from papertrail.pdf import split_pdf_bundles
            split_temp_dir = tempfile.TemporaryDirectory()
            non_split, split_pages, bundles_split = split_pdf_bundles(
                pdf_paths, Path(split_temp_dir.name), console,
            )
            if bundles_split > 0:
                split_pages_count = len(split_pages)
                pdf_paths = non_split + split_pages
                logger.debug(f"[PDF-SPLIT] Split {bundles_split} bundles into {split_pages_count} individual pages")
            else:
                logger.debug("[PDF-SPLIT] No splittable bundles found")

        # Process XLSX files first (deterministic, fast)
        xlsx_processed = 0
        xlsx_skipped = 0
        if xlsx_paths:
            xlsx_processed, xlsx_skipped = _process_xlsx_files(
                xlsx_paths, known_file_hashes, known_content_hashes,
                processed_path, console,
            )
            if not quiet and xlsx_processed > 0:
                console.success(f"{xlsx_processed} XLSX file(s) processed", indent=False)

        # Process PDF files
        logger.debug("Stage 1: Quick filtering using fast file hashes...")

        # Stage 1: Fast hashing with Rich progress
        fast_hash_map = {}
        for pdf in console.track(pdf_paths, "Fast hashing"):
            fast_hash_map[pdf] = hash_file_fast(pdf)

        potentially_new = [pdf for pdf in pdf_paths if fast_hash_map[pdf] not in known_file_hashes]

        # Intra-batch dedup by file hash
        potentially_new, batch_dedup_stage1 = dedup_batch(
            potentially_new, hash_fn=lambda pdf: fast_hash_map[pdf], label="Stage 1",
        )

        already_processed = len(pdf_paths) - len(potentially_new) - batch_dedup_stage1
        logger.debug(f"Stage 1: Skipped {already_processed} already-processed files (file hash match)")
        if batch_dedup_stage1 > 0:
            logger.debug(f"Stage 1: Skipped {batch_dedup_stage1} intra-batch duplicates (file hash)")
        logger.debug(f"{len(potentially_new)} files need further dedup checks")

        file_hash_duplicates = len(pdf_paths) - len(potentially_new) - batch_dedup_stage1

        if not potentially_new:
            if not quiet:
                console.success(f"{len(pdf_paths)} PDFs scanned, 0 new to process", indent=False)
            logger.debug("No new PDFs to process.")
            if temp_dir is not None:
                temp_dir.cleanup()
            if split_temp_dir is not None:
                split_temp_dir.cleanup()
            return {
                "pdf_scanned": len(pdf_paths),
                "xlsx_scanned": len(xlsx_paths),
                "new": xlsx_processed,
                "duplicates": file_hash_duplicates + xlsx_skipped,
                "batch_duplicates": 0,
                "failed": 0,
                "xlsx_new": xlsx_processed,
                "pdf_new": 0,
                "images_converted": images_converted,
                "bundles_split": bundles_split,
                "split_pages": split_pages_count,
                "new_issuers": [],
                "unknown_document_type": 0,
                "unknown_issuing_party": 0,
            }

        # Stage 2: Text-based hashing (fast, catches compression duplicates)
        logger.debug(f"Stage 2: Text-based hashing for {len(potentially_new)} files...")
        text_hash_map = {}
        for pdf in console.track(potentially_new, "Text hashing"):
            th = hash_file_text(pdf)
            if th is not None:
                text_hash_map[pdf] = th

        after_text = [pdf for pdf in potentially_new
                      if pdf not in text_hash_map or text_hash_map[pdf] not in known_text_hashes]
        text_duplicates = len(potentially_new) - len(after_text)
        if text_duplicates > 0:
            logger.debug(f"Stage 2: Skipped {text_duplicates} text-hash duplicates")

        # Intra-batch dedup by text hash
        after_text, batch_dedup_stage2 = dedup_batch(
            after_text, hash_fn=lambda pdf: text_hash_map.get(pdf), label="Stage 2",
        )
        if batch_dedup_stage2 > 0:
            logger.debug(f"Stage 2: Skipped {batch_dedup_stage2} intra-batch duplicates (text hash)")

        logger.debug(f"Stage 3: Content-based hashing for {len(after_text)} files...")
        content_hash_map = {}

        # Stage 3: Content hashing with Rich progress
        for pdf in console.track(after_text, "Content hashing"):
            try:
                content_hash = hash_file_content(pdf)
                content_hash_map[pdf] = content_hash
            except Exception as e:
                logger.error(f"Error hashing {pdf.name}: {e}")

        files_to_process = [pdf for pdf in after_text if content_hash_map.get(pdf) not in known_content_hashes]
        content_duplicates = len(after_text) - len(files_to_process)

        # Intra-batch dedup by content hash
        files_to_process, batch_dedup_stage3 = dedup_batch(
            files_to_process, hash_fn=lambda pdf: content_hash_map.get(pdf), label="Stage 3",
        )
        if batch_dedup_stage3 > 0:
            logger.debug(f"Stage 3: Skipped {batch_dedup_stage3} intra-batch duplicates (content hash)")

        batch_duplicates = batch_dedup_stage1 + batch_dedup_stage2 + batch_dedup_stage3
        logger.debug(f"Found {len(files_to_process)} truly new PDFs to process.")

        success_count = len(known_content_hashes)
        initial_count = success_count

        if files_to_process:
            rename_pdf_files(files_to_process, content_hash_map, known_content_hashes, known_file_hashes, processed_path,
                             failure_logger, doc_logger=doc_logger)

        new_processed = len(known_content_hashes) - initial_count
        failed = len(files_to_process) - new_processed
        elapsed = _time.monotonic() - run_start

        # Scan newly created files for new issuers and unknowns
        new_hashes = known_content_hashes - hashes_before
        new_issuers: set[str] = set()
        unknown_dt_count = 0
        unknown_ip_count = 0
        if new_hashes:
            for json_path, data in iter_json_files(processed_path):
                ch = data.get("hash_content")
                if ch not in new_hashes:
                    continue
                ip = data.get("issuing_party")
                if ip and ip != "$UNKNOWN$" and ip not in known_issuers_before:
                    new_issuers.add(ip)
                if data.get("document_type") == "$UNKNOWN$":
                    unknown_dt_count += 1
                if ip == "$UNKNOWN$":
                    unknown_ip_count += 1

        # Console output
        if not quiet:
            console.success(f"{len(pdf_paths)} PDFs scanned, {len(files_to_process)} new to process", indent=False)
            if new_processed > 0 or failed > 0:
                if failed > 0:
                    console.warning(f"{new_processed} processed, {failed} failed", indent=False)
                else:
                    console.success(f"{new_processed} processed successfully", indent=False)

        logger.debug(f"=== SUMMARY: {len(files_to_process)} attempted, {new_processed} success, {failed} failed, {elapsed:.1f}s total ===")

        if temp_dir is not None:
            temp_dir.cleanup()
        if split_temp_dir is not None:
            split_temp_dir.cleanup()

        return {
            "pdf_scanned": len(pdf_paths),
            "xlsx_scanned": len(xlsx_paths),
            "new": new_processed + xlsx_processed,
            "duplicates": file_hash_duplicates + text_duplicates + content_duplicates + xlsx_skipped + batch_duplicates,
            "batch_duplicates": batch_duplicates,
            "failed": failed,
            "xlsx_new": xlsx_processed,
            "pdf_new": new_processed,
            "images_converted": images_converted,
            "bundles_split": bundles_split,
            "split_pages": split_pages_count,
            "new_issuers": sorted(new_issuers),
            "unknown_document_type": unknown_dt_count,
            "unknown_issuing_party": unknown_ip_count,
        }


def _collect_sync_targets(processed_path: Path, all_unknown: bool = False,
                          pattern: str = None, orphans_only: bool = False) -> list[tuple]:
    """Collect files to sync. Returns list of (metadata_path, pdf_path, old_data_or_None)."""
    from papertrail.utils import make_matcher

    console = get_console()

    pdf_files = [f for f in processed_path.rglob("*.pdf")
                 if not any(part.startswith("_dupes") for part in f.parts)]
    if not pdf_files:
        logger.debug(f"No PDF files found in {processed_path}")
        return []

    targets = []

    if pattern:
        target_pdf = processed_path / pattern
        if target_pdf.exists() and target_pdf.suffix.lower() == '.pdf':
            target_json = target_pdf.with_suffix(".json")
            data = None
            if target_json.exists():
                if orphans_only:
                    return targets
                data = load_json_data(target_json)
            targets.append((target_json, target_pdf, data))
        else:
            matcher = make_matcher(pattern)
            for pdf_path in console.track(pdf_files, "Matching pattern"):
                if not matcher(pdf_path.name):
                    continue
                metadata_path = pdf_path.with_suffix(".json")
                has_metadata = metadata_path.exists()

                # Skip if orphans_only and has metadata
                if orphans_only and has_metadata:
                    continue

                data = None
                if has_metadata:
                    try:
                        data = load_json_data(metadata_path)
                    except Exception as e:
                        logger.warning(f"Failed to load {metadata_path.name}: {e}")
                targets.append((metadata_path, pdf_path, data))

    elif orphans_only:
        for pdf_path in console.track(pdf_files, "Scanning for orphans"):
            metadata_path = pdf_path.with_suffix(".json")
            if not metadata_path.exists():
                targets.append((metadata_path, pdf_path, None))

    if all_unknown:
        json_files = [f for f in processed_path.rglob("*.json")
                      if not any(part.startswith("_dupes") for part in f.parts)]
        for metadata_path in console.track(json_files, "Scanning for $UNKNOWN$"):
            try:
                data = load_json_data(metadata_path)
                has_unknown = (
                    data.get("document_type") == "$UNKNOWN$"
                    or data.get("issuing_party") == "$UNKNOWN$"
                    or data.get("date_issued") == "$UNKNOWN$"
                )
                if has_unknown:
                    pdf_path = metadata_path.with_suffix(".pdf")
                    if pdf_path.exists():
                        targets.append((metadata_path, pdf_path, data))
                    else:
                        logger.warning(f"PDF not found for {metadata_path.name}")
            except Exception as e:
                logger.warning(f"Skipping {metadata_path.name}: {e}")

    targets.sort(key=lambda t: t[1].name, reverse=True)
    return targets


def task_sync(processed_path: Path, dry_run: bool = False,
              all_unknown: bool = False, pattern: str = None,
              workers: int = 1, all: bool = False, quiet: bool = False) -> dict:
    """Sync metadata by running classification. Default: orphans only."""
    console = get_console()

    orphans_only = not all and not all_unknown and pattern is None

    with task_log_context(processed_path, "sync", show_header=not quiet):
        targets = _collect_sync_targets(processed_path, all_unknown=all_unknown,
                                        pattern=pattern, orphans_only=orphans_only)
        if not targets:
            if not quiet:
                console.warning("No files to sync", indent=False)
            return {"targets": 0}

        logger.debug(f"Found {len(targets)} files to sync (workers={workers})")

        def classify_one(item):
            metadata_path, pdf_path, old_data = item
            thread_doc_logger = DocumentLogger()
            try:
                if old_data is None:
                    content_hash = hash_file_content(pdf_path)
                    file_hash = hash_file_fast(pdf_path)
                    create_date = datetime.now().strftime("%Y-%m-%d")
                else:
                    content_hash = old_data.get("hash_content")
                    if not content_hash:
                        return (metadata_path, old_data, None, "No hash_content in metadata")
                    file_hash = old_data.get("hash_file")
                    create_date = old_data.get("date_created")

                new_metadata = classify_pdf_document(pdf_path, content_hash, doc_logger=thread_doc_logger)
                new_metadata.hash_file = file_hash
                new_metadata.hash_text = hash_file_text(pdf_path)
                new_metadata.date_created = create_date
                new_metadata.date_updated = datetime.now().strftime("%Y-%m-%d")
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
            nonlocal fixed_count, still_unknown_count, failed_count, new_count, renamed_count

            if error:
                logger.error(f"Failed {metadata_path.name}: {error}")
                failed_count += 1
                return

            new_doc_type = new_metadata.document_type
            new_issuer = new_metadata.issuing_party
            new_date = new_metadata.date_issued

            if old_data is None:
                logger.debug(f"New extraction: {metadata_path.name} -> {new_doc_type}, {new_issuer}, {new_date}")
                new_count += 1
            else:
                changes = []
                old_doc_type = old_data.get("document_type", "")
                old_issuer = old_data.get("issuing_party", "")
                old_date = old_data.get("date_issued", "")

                if old_doc_type != new_doc_type:
                    changes.append(f"document_type: {old_doc_type} -> {new_doc_type}")
                if old_issuer != new_issuer:
                    changes.append(f"issuing_party: {old_issuer} -> {new_issuer}")
                if old_date != new_date:
                    changes.append(f"date_issued: {old_date} -> {new_date}")

                if changes:
                    logger.debug(f"Changed {metadata_path.name}: {', '.join(changes)}")
                    fixed_count += 1
                else:
                    logger.debug(f"No changes: {metadata_path.name}")
                    still_unknown_count += 1

            if not dry_run:
                save_metadata_json(metadata_path.with_suffix(".pdf"), new_metadata)

                from papertrail.tasks.organize import file_name_from_metadata
                new_filename = file_name_from_metadata(new_metadata, new_metadata.hash_file)
                new_pdf_path = metadata_path.parent / new_filename
                old_pdf_path = metadata_path.with_suffix(".pdf")
                if old_pdf_path != new_pdf_path:
                    new_json_path = new_pdf_path.with_suffix(".json")
                    shutil.move(str(old_pdf_path), str(new_pdf_path))
                    shutil.move(str(metadata_path), str(new_json_path))
                    logger.debug(f"Renamed: {old_pdf_path.name} -> {new_pdf_path.name}")
                    renamed_count += 1

        def _truncated_name(path: Path) -> str:
            name = path.stem
            return name[:37] + "..." if len(name) > 40 else name

        if workers == 1:
            for i, item in enumerate(targets, 1):
                logger.debug(f"Syncing [{i}/{len(targets)}] {_truncated_name(item[1])}")
                metadata_path, old_data, new_metadata, error = classify_one(item)
                process_result(metadata_path, old_data, new_metadata, error)
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(classify_one, item): item for item in targets}
                with console.progress("Syncing", total=len(futures)) as progress:
                    task = progress.add_task("Syncing", total=len(futures))
                    for future in as_completed(futures):
                        item = futures[future]
                        progress.update(task, description=f"[dim]{_truncated_name(item[1])}[/dim]")
                        metadata_path, old_data, new_metadata, error = future.result()
                        process_result(metadata_path, old_data, new_metadata, error)
                        progress.update(task, advance=1)

        # Summary output
        if not quiet:
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

            if dry_run:
                console.detail("(dry run - no files were modified)", indent=False)

        logger.debug(f"New: {new_count}, Changed: {fixed_count}, Renamed: {renamed_count}, Unchanged: {still_unknown_count}, Failed: {failed_count}")

        return {
            "targets": len(targets),
            "new": new_count,
            "changed": fixed_count,
            "renamed": renamed_count,
            "unchanged": still_unknown_count,
            "failed": failed_count,
        }


