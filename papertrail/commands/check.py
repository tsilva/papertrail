"""Integrity checks, backfill, and audit reporting."""

from __future__ import annotations

from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from papertrail.engine import DocumentEngine
from papertrail.hashing import hash_file_content, hash_file_fast
from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.pdf import get_page_count
from papertrail.reconciliation_groundtruth import is_reconciliation_sidecar
from papertrail.repository import DocumentRepository
from papertrail.runtime import Runtime

logger = get_logger("check")

UNKNOWN = "$UNKNOWN$"

CRITICAL_FIELDS = [
    "hash_content",
    "hash_file",
    "hash_text",
    "date_issued",
    "document_type",
    "issuing_party",
    "document_title",
    "class_confidence",
    "page_count",
    "file_size_kb",
]

CONFIDENCE_BUCKETS = [
    (0.0, 0.5, "<0.5"),
    (0.5, 0.6, "0.5-0.6"),
    (0.6, 0.7, "0.6-0.7"),
    (0.7, 0.8, "0.7-0.8"),
    (0.8, 0.9, "0.8-0.9"),
    (0.9, 1.01, "0.9-1.0"),
]


def validate_metadata(runtime: Runtime, repository: DocumentRepository, output_path: Path):
    """Validate metadata files and companion hashes."""
    console = runtime.console
    valid_entries = []
    errors = []

    cache = runtime.hash_cache
    logger.debug(f"Hash cache loaded with {len(cache)} entries")

    pdf_info = []
    for metadata_path, pdf_path, metadata in repository.iter_documents(
        output_path,
        require_companion=False,
        validate=True,
    ):
        content_hash = metadata.hash_content
        if not content_hash:
            errors.append((metadata_path, "Missing 'hash_content' in metadata."))
            continue
        if not pdf_path.exists():
            errors.append((metadata_path, f"Missing PDF for metadata: {pdf_path.name}"))
            continue
        pdf_info.append((metadata_path, pdf_path, content_hash, metadata))

    if not pdf_info:
        if errors:
            for meta_path, err in errors:
                logger.warning(f"Validation error: {meta_path}: {err}")
        return valid_entries

    logger.debug(f"Computing fast hashes for {len(pdf_info)} PDFs...")
    hash_results = {}
    fast_hash_results = {}
    uncached = []

    for _, pdf_path, _, _ in console.track(pdf_info, "Fast hashing"):
        file_hash = hash_file_fast(pdf_path)
        fast_hash_results[pdf_path] = file_hash
        cached_content_hash = cache.get(file_hash)
        if cached_content_hash:
            hash_results[pdf_path] = cached_content_hash
        else:
            uncached.append((pdf_path, file_hash))

    if uncached:
        logger.debug(f"Computing content hashes for {len(uncached)} uncached PDFs...")
        with ProcessPoolExecutor() as executor:
            futures = {
                executor.submit(hash_file_content, pdf_path): (pdf_path, file_hash)
                for pdf_path, file_hash in uncached
            }
            with console.progress("Content hashing", total=len(futures)) as progress:
                task = progress.add_task("Content hashing", total=len(futures))
                for future in as_completed(futures):
                    pdf_path, file_hash = futures[future]
                    try:
                        content_hash = future.result()
                        hash_results[pdf_path] = content_hash
                        cache.set(file_hash, content_hash)
                    except Exception as exc:
                        for metadata_path, path, _, _ in pdf_info:
                            if path == pdf_path:
                                errors.append((metadata_path, f"Content hashing failed: {exc}"))
                                break
                    progress.update(task, advance=1)
        cache.save()
        logger.debug(f"Hash cache saved with {len(cache)} entries")

    for metadata_path, pdf_path, expected_hash, metadata in pdf_info:
        actual_content_hash = hash_results.get(pdf_path)
        if actual_content_hash is None:
            continue
        if expected_hash != actual_content_hash:
            errors.append(
                (
                    metadata_path,
                    f"Hash mismatch: metadata hash_content is '{expected_hash}', actual is '{actual_content_hash}'.",
                )
            )
            continue
        actual_file_hash = fast_hash_results.get(pdf_path)
        if metadata.hash_file and actual_file_hash and metadata.hash_file != actual_file_hash:
            errors.append(
                (
                    metadata_path,
                    f"Hash mismatch: metadata hash_file is '{metadata.hash_file}', actual is '{actual_file_hash}'.",
                )
            )
            continue
        if metadata.hash_file and metadata.hash_file not in pdf_path.name:
            errors.append(
                (
                    metadata_path,
                    f"Filename '{pdf_path.name}' does not include the expected hash_file '{metadata.hash_file}'.",
                )
            )
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
    merged_path = folder_path / "merged_all.pdf"
    if not merged_path.exists():
        return True
    source_pdfs = [
        path
        for path in folder_path.glob("*.pdf")
        if not path.name.startswith("merged_")
    ]
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


def run_check(
    runtime: Runtime,
    repository: DocumentRepository,
    engine: DocumentEngine,
    processed_path: Path,
    *,
    verify_hashes: bool = False,
    dry_run: bool = False,
) -> None:
    console = runtime.console
    setup_task_logging(processed_path, "check")

    if not dry_run:
        console.step("Fill missing metadata fields")
        stats = engine.backfill_processed(processed_path, dry_run=False)
        updated = stats["updated"]
        skipped = stats["skipped"]
        errors = stats["errors"]

        if updated > 0 or errors > 0:
            if errors > 0:
                console.warning(f"{updated} updated, {skipped} complete, {errors} errors", indent=False)
            else:
                console.success(f"{updated} updated, {skipped} already complete", indent=False)
        elif skipped > 0:
            console.success(f"{skipped} files already complete", indent=False)

    _task_audit(runtime, repository, processed_path, verify_hashes=verify_hashes)


def _bar(count: int, total: int, width: int = 30) -> str:
    if total == 0:
        return ""
    filled = round(count / total * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _pct(count: int, total: int) -> str:
    return "  0.0%" if total == 0 else f"{count / total * 100:5.1f}%"


def _header(title: str) -> None:
    print(f"\n{'=' * 60}\n  {title}\n{'=' * 60}")


def _is_sidecar(path: Path) -> bool:
    return is_reconciliation_sidecar(path)


def _load_all_metadata(repository: DocumentRepository, processed_path: Path) -> list[tuple[Path, dict]]:
    return [(path, data) for path, data in repository.iter_sidecars(processed_path) if not _is_sidecar(path)]


def _section_unknown(records, total):
    _header("1. $UNKNOWN$ Analysis")
    unknown_fields = ["document_type", "issuing_party", "date_issued"]
    field_counts = {field: sum(1 for _, data in records if data.get(field) == UNKNOWN) for field in unknown_fields}
    combined = sum(1 for _, data in records if any(data.get(field) == UNKNOWN for field in unknown_fields))

    print(f"\n  {'Field':<20} {'Count':>6}  {'Rate':>6}")
    print(f"  {'-' * 20} {'-' * 6}  {'-' * 6}")
    for field in unknown_fields:
        print(f"  {field:<20} {field_counts[field]:>6}  {_pct(field_counts[field], total)}")
    print(f"  {'(any field)':<20} {combined:>6}  {_pct(combined, total)}")

    unknown_type = [(path, data) for path, data in records if data.get("document_type") == UNKNOWN]
    if unknown_type:
        party_counter = Counter(data.get("issuing_party", UNKNOWN) for _, data in unknown_type)
        print("\n  Top issuing parties with unknown document_type:")
        for party, count in party_counter.most_common(10):
            print(f"    {party:<40} {count:>4}")

    unknown_party = [(path, data) for path, data in records if data.get("issuing_party") == UNKNOWN]
    if unknown_party:
        type_counter = Counter(data.get("document_type", UNKNOWN) for _, data in unknown_party)
        print("\n  Top document types with unknown issuing_party:")
        for dtype, count in type_counter.most_common(10):
            print(f"    {dtype:<40} {count:>4}")


def _section_confidence(records):
    _header("2. Confidence Distribution")
    confidences = []
    low_confidence = []
    for path, data in records:
        conf = data.get("class_confidence")
        if conf is not None:
            confidences.append(conf)
            if conf < 0.6:
                low_confidence.append((path.name, conf))

    if not confidences:
        print("\n  No confidence values found.")
        return

    bucket_counts = [(label, sum(1 for conf in confidences if lo <= conf < hi)) for lo, hi, label in CONFIDENCE_BUCKETS]
    max_count = max(count for _, count in bucket_counts) if bucket_counts else 1
    print(f"\n  {'Bucket':<10} {'Count':>6}  {'Rate':>6}  Distribution")
    print(f"  {'-' * 10} {'-' * 6}  {'-' * 6}  {'-' * 30}")
    for label, count in bucket_counts:
        print(f"  {label:<10} {count:>6}  {_pct(count, len(confidences))}  {_bar(count, max_count, 25)}")

    if low_confidence:
        low_confidence.sort(key=lambda row: row[1])
        print(f"\n  Files with confidence < 0.6 ({len(low_confidence)}):")
        for name, conf in low_confidence[:20]:
            print(f"    {conf:.2f}  {name}")
        if len(low_confidence) > 20:
            print(f"    ... and {len(low_confidence) - 20} more")


def _section_missing_fields(records, total):
    _header("3. Missing Critical Fields")
    missing = {field: sum(1 for _, data in records if data.get(field) is None) for field in CRITICAL_FIELDS}
    print(f"\n  {'Field':<20} {'Missing':>7}  {'Rate':>6}")
    print(f"  {'-' * 20} {'-' * 7}  {'-' * 6}")
    for field, count in missing.items():
        if count > 0:
            print(f"  {field:<20} {count:>7}  {_pct(count, total)}")


def _section_distribution(records, total, section_num, title, field):
    _header(f"{section_num}. {title}")
    counter = Counter(
        data.get(field, UNKNOWN)
        for _, data in records
        if data.get(field) not in (None, "")
    )
    print(f"\n  {'Value':<40} {'Count':>6}  {'Rate':>6}")
    print(f"  {'-' * 40} {'-' * 6}  {'-' * 6}")
    for value, count in counter.most_common(25):
        print(f"  {str(value)[:40]:<40} {count:>6}  {_pct(count, total)}")


def _section_dates(records):
    _header("6. Date Analysis")
    years = Counter()
    invalid = []
    for path, data in records:
        value = data.get("date_issued")
        if not value or value == UNKNOWN:
            continue
        if isinstance(value, str) and len(value) >= 4 and value[:4].isdigit():
            years[value[:4]] += 1
        else:
            invalid.append(path.name)

    print("\n  By year:")
    for year, count in sorted(years.items(), reverse=True):
        print(f"    {year}: {count}")
    if invalid:
        print(f"\n  Invalid date formats ({len(invalid)}):")
        for name in invalid[:10]:
            print(f"    {name}")


def _section_hash_dedup(records):
    _header("7. Duplicate Hash Audit")
    content_hashes = Counter(data.get("hash_content") for _, data in records if data.get("hash_content"))
    file_hashes = Counter(data.get("hash_file") for _, data in records if data.get("hash_file"))
    print(f"\n  Duplicate content hashes: {sum(1 for count in content_hashes.values() if count > 1)}")
    print(f"  Duplicate file hashes:    {sum(1 for count in file_hashes.values() if count > 1)}")


def _section_amounts(records):
    _header("8. Amount Analysis")
    amounts = [data.get("total_amount") for _, data in records if data.get("total_amount") is not None]
    currencies = Counter(data.get("total_amount_currency") for _, data in records if data.get("total_amount_currency"))
    if not amounts:
        print("\n  No amounts found.")
        return
    print(f"\n  Amount-bearing docs: {len(amounts)}")
    print(f"  Min amount:         {min(amounts):.2f}")
    print(f"  Max amount:         {max(amounts):.2f}")
    print("  Currencies:")
    for currency, count in currencies.most_common():
        print(f"    {currency}: {count}")


def _section_qr(records):
    _header("9. QR Coverage")
    qr_count = sum(1 for _, data in records if data.get("qrcode"))
    sub_doc_count = sum(1 for _, data in records if data.get("sub_documents"))
    print(f"\n  Documents with qrcode:       {qr_count}")
    print(f"  Documents with sub_documents:{sub_doc_count}")


def _section_summary(records, total):
    _header("10. Summary")
    unknown_any = sum(
        1
        for _, data in records
        if data.get("document_type") == UNKNOWN
        or data.get("issuing_party") == UNKNOWN
        or data.get("date_issued") == UNKNOWN
    )
    print(f"\n  Total sidecars:       {total}")
    print(f"  With any $UNKNOWN$:   {unknown_any}")
    print(f"  Fully specified:      {total - unknown_any}")


def _task_audit(runtime: Runtime, repository: DocumentRepository, processed_path: Path, *, verify_hashes: bool = False) -> None:
    console = runtime.console
    records = _load_all_metadata(repository, processed_path)
    total = len(records)

    if total == 0:
        console.warning("No metadata files found", indent=False)
        return

    if verify_hashes:
        console.step("Verify hashes")
        validate_metadata(runtime, repository, processed_path)

    _section_unknown(records, total)
    _section_confidence(records)
    _section_missing_fields(records, total)
    _section_distribution(records, total, 4, "Document Type Distribution", "document_type")
    _section_distribution(records, total, 5, "Issuing Party Distribution", "issuing_party")
    _section_dates(records)
    _section_hash_dedup(records)
    _section_amounts(records)
    _section_qr(records)
    _section_summary(records, total)
