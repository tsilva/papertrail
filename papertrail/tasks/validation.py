"""Validation tasks."""

import json
import re
from datetime import datetime
from pathlib import Path

from papertrail.console import get_console
from papertrail.hashing import hash_file_fast, hash_file_content, HashCache
from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.mappings import slugify_key
from papertrail.metadata import load_validated_metadata
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
            for meta_path, err in errors:
                logger.warning(f"Validation error: {meta_path}: {err}")
        return valid_entries

    # Phase 2: Compute fast hashes and check cache
    logger.debug(f"Computing fast hashes for {len(pdf_info)} PDFs...")
    hash_results = {}
    uncached = []

    with console.progress("Fast hashing", total=len(pdf_info)) as progress:
        task = progress.add_task("Fast hashing", total=len(pdf_info))
        for _, pdf_path, _, _ in pdf_info:
            file_hash = hash_file_fast(pdf_path)
            cached_content_hash = cache.get(file_hash)
            if cached_content_hash:
                hash_results[pdf_path] = cached_content_hash
            else:
                uncached.append((pdf_path, file_hash))
            progress.update(task, advance=1)

    cache_hits = len(pdf_info) - len(uncached)
    logger.debug(f"Cache hits: {cache_hits}, Cache misses: {len(uncached)}")

    # Phase 3: Parallel content hashing for uncached PDFs
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
                        for metadata_path, p, _, _ in pdf_info:
                            if p == pdf_path:
                                errors.append((metadata_path, f"Content hashing failed: {e}"))
                                break
                    progress.update(task, advance=1)

        cache.save()
        logger.debug(f"Hash cache saved with {len(cache)} entries")

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


def check_files_exist(target_folder: Path, validation_schema_path: Path):
    """Validate files exist based on a schema."""
    from papertrail.metadata import load_json_data

    console = get_console()

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

    passed_count = sum(1 for found, _, _ in check_results if found)
    missing_count = len(check_results) - passed_count
    all_passed = missing_count == 0

    # Build validation table rows
    table_rows = []
    sorted_results = sorted(check_results, key=lambda x: (not x[0], x[1]))
    for found, idx, check in sorted_results:
        # Build a readable description from the check criteria
        desc_parts = []
        if "document_type" in check:
            desc_parts.append(check["document_type"])
        if "issuing_party" in check:
            desc_parts.append(f"({check['issuing_party']})")
        if "service_name" in check:
            desc_parts.append(f"[{check['service_name']}]")

        description = " ".join(desc_parts) if desc_parts else str(check)
        status = "FOUND" if found else "MISSING"
        table_rows.append({"description": description, "found": found})

        # Log to file
        logger.debug(f"{'[OK]' if found else '[FAIL]'} {check} -- {'FOUND' if found else 'NOT FOUND'}")

    # Display validation table
    console.validation_table("File Validation Results", table_rows)

    # Summary
    if all_passed:
        console.success(f"{passed_count} checks passed", indent=False)
    else:
        console.warning(f"{passed_count} checks passed, {missing_count} missing", indent=False)


def task_backfill_page_count(processed_path: Path):
    """Backfill page_count for existing metadata files that don't have it."""
    console = get_console()
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
        console.warning("No metadata files found", indent=False)
        return

    if errors > 0:
        console.warning(f"{updated} updated, {skipped} skipped, {errors} errors", indent=False)
    else:
        console.success(f"{updated} updated, {skipped} skipped (already had page_count)", indent=False)

    logger.debug(f"Backfill complete: {updated} updated, {skipped} skipped, {errors} errors")


def task_backfill_mapping_keys(processed_path: Path):
    """Backfill document_type_key and issuing_party_key for existing metadata files."""
    console = get_console()
    setup_task_logging(processed_path, "backfill_mapping_keys")

    updated = 0
    skipped = 0
    errors = 0

    for metadata_path, pdf_path, data in load_validated_metadata(
        processed_path, require_pdf=False, validate=False, show_progress=True, progress_desc="Backfilling mapping keys"
    ):
        try:
            doc_type_raw = data.get("document_type_raw")
            issuer_raw = data.get("issuing_party_raw")

            expected_dt_key = slugify_key(doc_type_raw) if doc_type_raw else None
            expected_ip_key = slugify_key(issuer_raw) if issuer_raw else None

            if data.get("document_type_key") == expected_dt_key and data.get("issuing_party_key") == expected_ip_key:
                skipped += 1
                continue

            if expected_dt_key is not None:
                data["document_type_key"] = expected_dt_key
            if expected_ip_key is not None:
                data["issuing_party_key"] = expected_ip_key
            data["update_date"] = datetime.now().strftime("%Y-%m-%d")

            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

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
        console.success(f"{updated} updated, {skipped} skipped (already had mapping keys)", indent=False)

    logger.debug(f"Backfill mapping keys complete: {updated} updated, {skipped} skipped, {errors} errors")


_MONTH_NAMES = {
    'jan', 'fev', 'mar', 'abr', 'mai', 'jun', 'jul', 'ago', 'set', 'out', 'nov', 'dez',
    'janeiro', 'fevereiro', 'marco', 'abril', 'maio', 'junho',
    'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro',
    'feb', 'apr', 'may', 'aug', 'sep', 'oct', 'dec',
    'january', 'february', 'march', 'april', 'june',
    'july', 'august', 'september', 'october', 'november', 'december',
}


def has_temporal_tokens(key: str) -> bool:
    """Check if a slugified key contains temporal tokens (month names or digit-only segments)."""
    parts = key.split('-')
    return any(p in _MONTH_NAMES or re.fullmatch(r'\d+', p) for p in parts)


def task_tag_dated_types(processed_path: Path, dry_run: bool = False):
    """Tag documents whose document_type_key contains date/temporal patterns.

    Sets document_type to $UNKNOWN$ so they can be re-extracted with sync --all_unknown.
    """
    console = get_console()
    setup_task_logging(processed_path, "tag_dated_types")

    tagged = 0
    skipped = 0
    errors = 0

    for metadata_path, pdf_path, data in load_validated_metadata(
        processed_path, require_pdf=False, validate=False, show_progress=True, progress_desc="Scanning for dated types"
    ):
        try:
            dt_key = data.get("document_type_key")
            if not dt_key or not has_temporal_tokens(dt_key):
                skipped += 1
                continue

            if dry_run:
                logger.info(f"[DRY-RUN] Would tag: {metadata_path.name} (key: {dt_key})")
                tagged += 1
                continue

            data["document_type"] = "$UNKNOWN$"
            data["update_date"] = datetime.now().strftime("%Y-%m-%d")

            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            logger.info(f"Tagged: {metadata_path.name} (key: {dt_key})")
            tagged += 1

        except Exception as e:
            logger.error(f"Failed to process {metadata_path.name}: {e}")
            errors += 1

    if tagged == 0 and skipped == 0 and errors == 0:
        console.warning("No metadata files found", indent=False)
        return

    prefix = "[DRY-RUN] " if dry_run else ""
    if errors > 0:
        console.warning(f"{prefix}{tagged} tagged, {skipped} skipped, {errors} errors", indent=False)
    else:
        console.success(f"{prefix}{tagged} tagged, {skipped} skipped (no temporal pattern)", indent=False)


def task_backfill_document_title(processed_path: Path):
    """Backfill document_title for existing metadata files that don't have it.

    Sets document_title = document_type_raw for existing docs, since document_type_raw
    previously contained the full heading text that document_title should hold going forward.
    """
    console = get_console()
    setup_task_logging(processed_path, "backfill_document_title")

    updated = 0
    skipped = 0
    errors = 0

    for metadata_path, pdf_path, data in load_validated_metadata(
        processed_path, require_pdf=False, validate=False, show_progress=True, progress_desc="Backfilling document_title"
    ):
        try:
            if data.get("document_title") is not None:
                skipped += 1
                continue

            doc_type_raw = data.get("document_type_raw")
            if not doc_type_raw:
                skipped += 1
                continue

            data["document_title"] = doc_type_raw
            data["update_date"] = datetime.now().strftime("%Y-%m-%d")

            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

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
        console.success(f"{updated} updated, {skipped} skipped (already had document_title)", indent=False)

    logger.debug(f"Backfill document_title complete: {updated} updated, {skipped} skipped, {errors} errors")
