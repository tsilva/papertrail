"""Validation tasks."""

import json
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

from papertrail.hashing import hash_file_fast, hash_file_content, HashCache
from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.metadata import load_validated_metadata
from papertrail.models import DocumentMetadata
from papertrail.pdf import get_page_count

logger = get_logger('cli')


def validate_metadata(output_path: Path):
    """Validate metadata files and their corresponding PDFs."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    valid_entries = []
    errors = []

    cache = HashCache()
    logger.info(f"Hash cache loaded with {len(cache)} entries")

    # Phase 1: Collect all PDF paths and their expected hashes using the helper
    pdf_info = []
    for metadata_path, pdf_path, metadata in load_validated_metadata(
        output_path, require_pdf=False, validate=True
    ):
        try:
            content_hash = metadata.content_hash
            if not content_hash:
                errors.append((metadata_path, "Missing 'content_hash' in metadata."))
                continue

            if not pdf_path.exists():
                errors.append((metadata_path, f"Missing PDF for metadata: {pdf_path.name}"))
                continue

            pdf_info.append((metadata_path, pdf_path, content_hash, metadata))

        except Exception as e:
            errors.append((metadata_path, str(e)))

    if not pdf_info:
        if errors:
            logger.warning("Validation errors found:")
            for meta_path, err in errors:
                logger.warning(f"- {meta_path}: {err}")
        return valid_entries

    # Phase 2: Compute fast hashes and check cache
    logger.info(f"Computing fast hashes for {len(pdf_info)} PDFs...")
    hash_results = {}
    uncached = []

    for _, pdf_path, _, _ in tqdm(pdf_info, desc="Fast hashing"):
        file_hash = hash_file_fast(pdf_path)
        cached_content_hash = cache.get(file_hash)
        if cached_content_hash:
            hash_results[pdf_path] = cached_content_hash
        else:
            uncached.append((pdf_path, file_hash))

    cache_hits = len(pdf_info) - len(uncached)
    logger.info(f"  -> Cache hits: {cache_hits}, Cache misses: {len(uncached)}")

    # Phase 3: Parallel content hashing for uncached PDFs
    if uncached:
        logger.info(f"Computing content hashes for {len(uncached)} uncached PDFs...")
        with ProcessPoolExecutor() as executor:
            futures = {executor.submit(hash_file_content, pdf_path): (pdf_path, file_hash)
                       for pdf_path, file_hash in uncached}

            for future in tqdm(as_completed(futures), total=len(futures), desc="Content hashing"):
                pdf_path, file_hash = futures[future]
                try:
                    content_hash = future.result()
                    hash_results[pdf_path] = content_hash
                    cache.set(file_hash, content_hash)
                except Exception as e:
                    for metadata_path, p, _, _ in pdf_info:
                        if p == pdf_path:
                            errors.append((metadata_path, f"Content hashing failed: {e}"))
                            break

        cache.save()
        logger.info(f"Hash cache saved with {len(cache)} entries")

    # Phase 4: Validate using precomputed hashes
    for metadata_path, pdf_path, expected_hash, metadata in pdf_info:
        actual_hash = hash_results.get(pdf_path)
        if actual_hash is None:
            continue

        if expected_hash != actual_hash:
            errors.append((metadata_path, f"Hash mismatch: metadata content_hash is '{expected_hash}', actual is '{actual_hash}'."))
            continue

        if expected_hash not in pdf_path.name:
            errors.append((metadata_path, f"Filename '{pdf_path.name}' does not include the expected hash '{expected_hash}'."))
            continue

        valid_entries.append((pdf_path, metadata))

    if errors:
        logger.warning("Validation errors found:")
        for meta_path, err in errors:
            logger.warning(f"- {meta_path}: {err}")
    else:
        logger.info("All metadata files passed validation.")

    return valid_entries


def validate_merged_pdf(folder_path: Path) -> bool:
    """Validate that merged_all.pdf has the correct page count."""
    merged_path = folder_path / "merged_all.pdf"
    if not merged_path.exists():
        logger.info(f"  No merged_all.pdf found in {folder_path}")
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

    logger.info(f"  Merge validation passed: {actual_pages} pages from {len(source_pdfs)} files")
    return True


def check_files_exist(target_folder: Path, validation_schema_path: Path):
    """Validate files exist based on a schema."""
    from papertrail.metadata import load_json_data

    with open(validation_schema_path, "r", encoding="utf-8") as f:
        checks = json.load(f)

    file_data = []
    for json_path, _, data in load_validated_metadata(target_folder, require_pdf=False, validate=False):
        file_data.append((json_path, data))

    check_results = []
    for idx, check in enumerate(checks):
        found = any(
            all(str(data.get(k, "")).strip() == str(v).strip() for k, v in check.items())
            for _, data in file_data
        )
        check_results.append((found, idx, check))

    all_passed = all(found for found, _, _ in check_results)

    sorted_results = sorted(check_results, key=lambda x: (not x[0], x[1]))
    for found, idx, check in sorted_results:
        status = "[OK]" if found else "[FAIL]"
        result = "FOUND" if found else "NOT FOUND"
        if found:
            logger.info(f"{status} {check} -- {result}")
        else:
            logger.warning(f"{status} {check} -- {result}")

    if all_passed:
        logger.info("All file existence checks passed.")
    else:
        logger.warning("Some file existence checks failed.")


def task_backfill_page_count(processed_path: Path):
    """Backfill page_count for existing metadata files that don't have it."""
    setup_task_logging(processed_path, "backfill_page_count")

    updated = 0
    skipped = 0
    errors = 0

    for metadata_path, pdf_path, data in load_validated_metadata(
        processed_path, require_pdf=True, validate=False, show_progress=True, progress_desc="Backfilling page_count"
    ):
        try:
            if data.get("page_count") is not None:
                skipped += 1
                continue

            page_count = get_page_count(pdf_path)
            data["page_count"] = page_count
            data["update_date"] = datetime.now().strftime("%Y-%m-%d")

            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            updated += 1

        except Exception as e:
            logger.error(f"Failed to process {metadata_path.name}: {e}")
            errors += 1

    if updated == 0 and skipped == 0 and errors == 0:
        logger.info(f"No metadata files found in {processed_path}")
        return

    logger.info(f"Backfill complete: {updated} updated, {skipped} skipped (already had page_count), {errors} errors")
