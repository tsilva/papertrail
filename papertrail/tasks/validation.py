"""Validation tasks."""

from datetime import datetime
from pathlib import Path

from papertrail.console import get_console
from papertrail.hashing import hash_file_fast, hash_file_content, hash_file_text, HashCache
from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.metadata import load_validated_metadata, save_json_data
from papertrail.models import DocumentMetadata
from papertrail.pdf import get_page_count

logger = get_logger('cli')


def validate_metadata(output_path: Path):
    """Validate metadata files and their corresponding PDFs."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    console = get_console()
    valid_entries = []
    errors = []

    cache = HashCache()
    logger.debug(f"Hash cache loaded with {len(cache)} entries")

    pdf_info = []
    for metadata_path, pdf_path, metadata in load_validated_metadata(
        output_path, require_pdf=False, validate=True
    ):
        try:
            content_hash = metadata.hash_content
            if not content_hash:
                errors.append((metadata_path, "Missing 'hash_content' in metadata."))
                continue

            if not pdf_path.exists():
                errors.append((metadata_path, f"Missing PDF for metadata: {pdf_path.name}"))
                continue

            pdf_info.append((metadata_path, pdf_path, content_hash, metadata, None))

        except Exception as e:
            errors.append((metadata_path, str(e)))

    if not pdf_info:
        if errors:
            for meta_path, err in errors:
                logger.warning(f"Validation error: {meta_path}: {err}")
        return valid_entries

    logger.debug(f"Computing fast hashes for {len(pdf_info)} PDFs...")
    hash_results = {}
    fast_hash_results = {}
    uncached = []

    for _, pdf_path, _, _, _ in console.track(pdf_info, "Fast hashing"):
        file_hash = hash_file_fast(pdf_path)
        fast_hash_results[pdf_path] = file_hash
        cached_content_hash = cache.get(file_hash)
        if cached_content_hash:
            hash_results[pdf_path] = cached_content_hash
        else:
            uncached.append((pdf_path, file_hash))

    cache_hits = len(pdf_info) - len(uncached)
    logger.debug(f"Cache hits: {cache_hits}, Cache misses: {len(uncached)}")

    if uncached:
        logger.debug(f"Computing content hashes for {len(uncached)} uncached PDFs...")
        with ProcessPoolExecutor() as executor:
            futures = {executor.submit(hash_file_content, pdf_path): (pdf_path, file_hash)
                       for pdf_path, file_hash in uncached}

            with console.progress("Content hashing", total=len(futures)) as progress:
                task = progress.add_task("Content hashing", total=len(futures))
                for future in as_completed(futures):
                    pdf_path, file_hash = futures[future]
                    try:
                        content_hash = future.result()
                        hash_results[pdf_path] = content_hash
                        cache.set(file_hash, content_hash)
                    except Exception as e:
                        for metadata_path, p, _, _, _ in pdf_info:
                            if p == pdf_path:
                                errors.append((metadata_path, f"Content hashing failed: {e}"))
                                break
                    progress.update(task, advance=1)

        cache.save()
        logger.debug(f"Hash cache saved with {len(cache)} entries")

    for metadata_path, pdf_path, expected_hash, metadata, _ in pdf_info:
        actual_content_hash = hash_results.get(pdf_path)
        if actual_content_hash is None:
            continue

        if expected_hash != actual_content_hash:
            errors.append((metadata_path, f"Hash mismatch: metadata hash_content is '{expected_hash}', actual is '{actual_content_hash}'."))
            continue

        actual_file_hash = fast_hash_results.get(pdf_path)
        if metadata.hash_file and actual_file_hash and metadata.hash_file != actual_file_hash:
            errors.append((metadata_path, f"Hash mismatch: metadata hash_file is '{metadata.hash_file}', actual is '{actual_file_hash}'."))
            continue

        if metadata.hash_file and metadata.hash_file not in pdf_path.name:
            errors.append((metadata_path, f"Filename '{pdf_path.name}' does not include the expected hash_file '{metadata.hash_file}'."))
            continue

        valid_entries.append((pdf_path, metadata))

    if errors:
        for meta_path, err in errors:
            logger.warning(f"Validation error: {meta_path}: {err}")
        console.warning(f"{len(valid_entries)} valid, {len(errors)} errors", indent=False)
    else:
        console.success(f"{len(valid_entries)} files validated", indent=False)

    return valid_entries


def validate_merged_pdf(folder_path: Path) -> bool:
    """Validate that merged_all.pdf has the correct page count."""
    merged_path = folder_path / "merged_all.pdf"
    if not merged_path.exists():
        logger.debug(f"No merged_all.pdf found in {folder_path}")
        return True

    source_pdfs = [p for p in folder_path.glob("*.pdf") if p.name != "merged_all.pdf"]
    expected_pages = sum(get_page_count(pdf) for pdf in source_pdfs)

    actual_pages = get_page_count(merged_path)

    if actual_pages != expected_pages:
        raise AssertionError(
            f"Merged PDF page count mismatch in {folder_path}: "
            f"expected {expected_pages} pages (from {len(source_pdfs)} files), "
            f"got {actual_pages} pages"
        )

    logger.debug(f"Merge validation passed: {actual_pages} pages from {len(source_pdfs)} files")
    return True


def _batch_update_metadata(
    processed_path: Path,
    task_name: str,
    progress_desc: str,
    require_pdf: bool,
    should_skip,
    update_fn,
    skip_label: str,
):
    """Generic batch metadata updater with skip/update callbacks."""
    console = get_console()
    setup_task_logging(processed_path, task_name)

    updated = 0
    skipped = 0
    errors = 0

    for metadata_path, pdf_path, data in load_validated_metadata(
        processed_path, require_pdf=require_pdf, validate=False,
        show_progress=True, progress_desc=progress_desc,
    ):
        try:
            if should_skip(metadata_path, pdf_path, data):
                skipped += 1
                continue

            update_fn(metadata_path, pdf_path, data)
            save_json_data(metadata_path, data)

            updated += 1

        except Exception as e:
            logger.error(f"Failed to process {metadata_path.name}: {e}")
            errors += 1

    if updated == 0 and skipped == 0 and errors == 0:
        console.warning("No metadata files found", indent=False)
        return

    if errors > 0:
        console.warning(f"{updated} updated, {skipped} skipped, {errors} errors", indent=False)
    else:
        console.success(f"{updated} updated, {skipped} skipped ({skip_label})", indent=False)

    logger.debug(f"{task_name} complete: {updated} updated, {skipped} skipped, {errors} errors")


def task_backfill_page_count(processed_path: Path):
    """Backfill page_count for existing metadata files that don't have it."""
    def _update(metadata_path, pdf_path, data):
        data["page_count"] = get_page_count(pdf_path)
        data["date_updated"] = datetime.now().strftime("%Y-%m-%d")

    _batch_update_metadata(
        processed_path,
        task_name="backfill_page_count",
        progress_desc="Backfilling page_count",
        require_pdf=True,
        should_skip=lambda _mp, _pp, data: data.get("page_count") is not None,
        update_fn=_update,
        skip_label="already had page_count",
    )


def task_backfill_file_size(processed_path: Path):
    """Backfill file_size_kb for existing metadata files that don't have it."""
    from papertrail.metadata import find_companion_file

    def _should_skip(_mp, _pp, data):
        return data.get("file_size_kb") is not None

    def _update(metadata_path, _pdf_path, data):
        companion = find_companion_file(metadata_path, data)
        if companion is None:
            return
        data["file_size_kb"] = round(companion.stat().st_size / 1024)
        data["date_updated"] = datetime.now().strftime("%Y-%m-%d")

    _batch_update_metadata(
        processed_path,
        task_name="backfill_file_size",
        progress_desc="Backfilling file_size_kb",
        require_pdf=False,
        should_skip=_should_skip,
        update_fn=_update,
        skip_label="already had file_size_kb",
    )


def task_fix_unicode(processed_path: Path):
    """Fix Unicode escape sequences in metadata JSON files."""
    def _should_skip(metadata_path, _pdf_path, _data):
        raw_content = metadata_path.read_text(encoding="utf-8")
        return "\\u00" not in raw_content

    _batch_update_metadata(
        processed_path,
        task_name="fix_unicode",
        progress_desc="Fixing Unicode escapes",
        require_pdf=False,
        should_skip=_should_skip,
        update_fn=lambda _mp, _pp, _data: None,  # re-saving with ensure_ascii=False is the fix
        skip_label="already UTF-8",
    )


def task_backfill_text_hash(processed_path: Path):
    """Backfill hash_text for existing metadata files that don't have it."""
    from papertrail.metadata import find_companion_file

    def _should_skip(_mp, _pp, data):
        return "hash_text" in data

    def _update(metadata_path, _pdf_path, data):
        companion = find_companion_file(metadata_path, data)
        if companion is None:
            return
        if companion.suffix.lower() != ".pdf":
            data["hash_text"] = None
        else:
            data["hash_text"] = hash_file_text(companion)
        data["date_updated"] = datetime.now().strftime("%Y-%m-%d")

    _batch_update_metadata(
        processed_path,
        task_name="backfill_text_hash",
        progress_desc="Backfilling hash_text",
        require_pdf=False,
        should_skip=_should_skip,
        update_fn=_update,
        skip_label="already had hash_text",
    )


def task_backfill_sub_documents(processed_path: Path):
    """Backfill sub_documents for PDFs with multiple Portuguese invoice QR codes.

    Scans each PDF for QR codes. If 2+ Portuguese invoice QR codes found,
    builds sub_documents list from QR data and sets parent qrcode to None.
    NIF enrichment is skipped for speed — use sync afterward for full enrichment.
    """
    from papertrail.metadata import find_companion_file
    from papertrail.qr import extract_all_metadata_from_qr
    from papertrail.models import SubDocumentMetadata

    def _should_skip(_mp, _pp, data):
        return data.get("sub_documents") is not None

    def _update(metadata_path, _pdf_path, data):
        companion = find_companion_file(metadata_path, data)
        if companion is None or companion.suffix.lower() != ".pdf":
            data["sub_documents"] = None
            data["date_updated"] = datetime.now().strftime("%Y-%m-%d")
            return

        all_results = extract_all_metadata_from_qr(companion)
        if len(all_results) < 2:
            data["sub_documents"] = None
            data["date_updated"] = datetime.now().strftime("%Y-%m-%d")
            return

        sub_docs = []
        for qr_metadata, qr_raw_data in all_results:
            sub_doc = SubDocumentMetadata(
                date_issued=qr_metadata.issue_date,
                document_type=qr_metadata.document_type,
                total_amount=qr_metadata.total_amount,
                total_amount_currency=qr_metadata.total_amount_currency,
                issuer_tax_number=qr_metadata.issuer_tax_number,
                document_number=qr_metadata.document_number,
                atcud=qr_metadata.atcud,
                locale=qr_metadata.locale,
                qrcode=qr_raw_data,
            )
            sub_docs.append(sub_doc.model_dump())

        data["sub_documents"] = sub_docs
        data["qrcode"] = None
        data["date_updated"] = datetime.now().strftime("%Y-%m-%d")
        logger.debug(f"[MULTI-QR] {companion.name}: {len(sub_docs)} sub-documents")

    _batch_update_metadata(
        processed_path,
        task_name="backfill_sub_documents",
        progress_desc="Backfilling sub_documents",
        require_pdf=False,
        should_skip=_should_skip,
        update_fn=_update,
        skip_label="already processed",
    )


def task_split_bundles(processed_path: Path, dry_run: bool = False):
    """Find multi-page PDFs that are bundles of independent single-page documents and split them.

    Splits each bundle into individual pages, processes them through the extraction pipeline,
    and archives the original bundles to _dupes/split_bundles/.
    """
    import shutil
    import tempfile

    from papertrail.metadata import find_companion_file, load_validated_metadata
    from papertrail.pdf_split import is_splittable_bundle, split_pdf_bundle

    console = get_console()
    setup_task_logging(processed_path, "split_bundles")

    # Scan for multi-page PDFs
    candidates = []
    for metadata_path, pdf_path, data in load_validated_metadata(
        processed_path, require_pdf=False, validate=False,
        show_progress=True, progress_desc="Scanning for bundles",
    ):
        page_count = data.get("page_count")
        if page_count is not None and page_count <= 1:
            continue
        companion = find_companion_file(metadata_path, data)
        if companion is None or companion.suffix.lower() != ".pdf":
            continue
        candidates.append((metadata_path, companion, data))

    if not candidates:
        console.warning("No multi-page PDFs found", indent=False)
        return

    logger.debug(f"Found {len(candidates)} multi-page PDFs to check")

    # Check which are splittable
    splittable = []
    for metadata_path, pdf_path, data in console.track(candidates, "Checking pagination"):
        if is_splittable_bundle(pdf_path):
            splittable.append((metadata_path, pdf_path, data))

    not_splittable = len(candidates) - len(splittable)
    console.info(
        f"{len(splittable)} splittable bundles, {not_splittable} genuine multi-page",
        indent=False,
    )

    if not splittable:
        return

    if dry_run:
        total_pages = sum(d.get("page_count", 0) for _, _, d in splittable)
        console.detail(f"Would split into ~{total_pages} individual pages (dry run)", indent=False)
        for _, pdf_path, data in splittable:
            logger.debug(f"[PDF-SPLIT] Would split: {pdf_path.name} ({data.get('page_count', '?')} pages)")
        return

    # Split and process
    from papertrail.tasks.extraction import task_extract_new

    temp_dir = tempfile.TemporaryDirectory()
    temp_path = Path(temp_dir.name)

    split_count = 0
    pages_created = 0
    for _, pdf_path, _ in console.track(splittable, "Splitting bundles"):
        try:
            pages = split_pdf_bundle(pdf_path, temp_path)
            pages_created += len(pages)
            split_count += 1
        except Exception as e:
            logger.error(f"[PDF-SPLIT] Failed to split {pdf_path.name}: {e}")

    console.success(f"Split {split_count} bundles into {pages_created} pages", indent=False)

    # Process split pages through extraction pipeline
    console.step("Classifying split pages")
    extract_stats = task_extract_new(processed_path, [temp_path], quiet=False)
    temp_dir.cleanup()

    if extract_stats is None:
        console.warning("Extraction locked by another process, originals preserved", indent=False)
        return

    new = extract_stats.get("new", 0)
    if new == 0:
        console.warning("No split pages were successfully extracted, originals preserved", indent=False)
        return

    # Archive original bundles
    archive_dir = processed_path / "_dupes" / "split_bundles"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archived = 0
    for metadata_path, pdf_path, _ in splittable:
        try:
            shutil.move(str(pdf_path), str(archive_dir / pdf_path.name))
            if metadata_path.exists():
                shutil.move(str(metadata_path), str(archive_dir / metadata_path.name))
            archived += 1
        except Exception as e:
            logger.error(f"Failed to archive {pdf_path.name}: {e}")

    console.success(
        f"{archived} bundles archived to _dupes/split_bundles/",
        indent=False,
    )
    new = extract_stats.get("new", 0)
    dupes = extract_stats.get("duplicates", 0)
    logger.debug(f"split_bundles complete: {split_count} split, {pages_created} pages, {new} new, {dupes} deduped, {archived} archived")
