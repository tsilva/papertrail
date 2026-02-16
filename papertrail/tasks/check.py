"""Unified check: validation, backfill, and audit reporting."""

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from papertrail.console import get_console
from papertrail.hashing import hash_file_fast, hash_file_content, hash_file_text, HashCache
from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.metadata import (
    load_validated_metadata, save_json_data, find_companion_file, iter_json_files,
)
from papertrail.models import DocumentMetadata
from papertrail.pdf import get_page_count

logger = get_logger('cli')

UNKNOWN = "$UNKNOWN$"


# --- Validation ---

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


# --- Unified check command ---

def task_check(processed_path: Path, verify_hashes: bool = False, dry_run: bool = False) -> None:
    """Unified check: fill missing fields, verify integrity, run audit report."""
    console = get_console()
    setup_task_logging(processed_path, "check")

    if not dry_run:
        console.step("Fill missing metadata fields")
        now = datetime.now().strftime("%Y-%m-%d")
        updated = skipped = errors = 0

        for metadata_path, pdf_path, data in load_validated_metadata(
            processed_path, require_pdf=False, validate=False,
            show_progress=True, progress_desc="Checking metadata",
        ):
            try:
                changed = False

                if data.get("page_count") is None:
                    companion = find_companion_file(metadata_path, data)
                    if companion and companion.suffix.lower() == ".pdf":
                        data["page_count"] = get_page_count(companion)
                        changed = True

                if data.get("file_size_kb") is None:
                    companion = find_companion_file(metadata_path, data)
                    if companion and companion.exists():
                        data["file_size_kb"] = round(companion.stat().st_size / 1024)
                        changed = True

                if "hash_text" not in data:
                    companion = find_companion_file(metadata_path, data)
                    if companion:
                        data["hash_text"] = hash_file_text(companion) if companion.suffix.lower() == ".pdf" else None
                        changed = True

                if data.get("sub_documents") is None and "sub_documents" not in data:
                    companion = find_companion_file(metadata_path, data)
                    if companion and companion.suffix.lower() == ".pdf":
                        from papertrail.qr import extract_all_metadata_from_qr
                        from papertrail.models import SubDocumentMetadata
                        all_results = extract_all_metadata_from_qr(companion)
                        if len(all_results) >= 2:
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
                        else:
                            data["sub_documents"] = None
                        changed = True
                    elif companion:
                        data["sub_documents"] = None
                        changed = True

                if changed:
                    data["date_updated"] = now
                    save_json_data(metadata_path, data)
                    updated += 1
                else:
                    skipped += 1

            except Exception as e:
                logger.error(f"Failed to process {metadata_path.name}: {e}")
                errors += 1

        if updated > 0 or errors > 0:
            if errors > 0:
                console.warning(f"{updated} updated, {skipped} complete, {errors} errors", indent=False)
            else:
                console.success(f"{updated} updated, {skipped} already complete", indent=False)
        elif skipped > 0:
            console.success(f"{skipped} files already complete", indent=False)

    # Audit report (always runs)
    _task_audit(processed_path, verify_hashes=verify_hashes)


# --- Audit report ---

CRITICAL_FIELDS = [
    "hash_content", "hash_file", "hash_text", "date_issued",
    "document_type", "issuing_party", "document_title",
    "class_confidence", "page_count", "file_size_kb",
]

CONFIDENCE_BUCKETS = [
    (0.0, 0.5, "<0.5"), (0.5, 0.6, "0.5-0.6"), (0.6, 0.7, "0.6-0.7"),
    (0.7, 0.8, "0.7-0.8"), (0.8, 0.9, "0.8-0.9"), (0.9, 1.01, "0.9-1.0"),
]


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
    name = path.name
    return name.endswith(".reconciliation.json") or name.endswith(".embeddings.json")


def _load_all_metadata(processed_path: Path) -> list[tuple[Path, dict]]:
    return [(p, d) for p, d in iter_json_files(processed_path) if not _is_sidecar(p)]


def _section_unknown(records, total):
    _header("1. $UNKNOWN$ Analysis")
    unknown_fields = ["document_type", "issuing_party", "date_issued"]
    field_counts = {f: sum(1 for _, d in records if d.get(f) == UNKNOWN) for f in unknown_fields}
    combined = sum(1 for _, d in records if any(d.get(f) == UNKNOWN for f in unknown_fields))

    print(f"\n  {'Field':<20} {'Count':>6}  {'Rate':>6}")
    print(f"  {'-' * 20} {'-' * 6}  {'-' * 6}")
    for f in unknown_fields:
        print(f"  {f:<20} {field_counts[f]:>6}  {_pct(field_counts[f], total)}")
    print(f"  {'(any field)':<20} {combined:>6}  {_pct(combined, total)}")

    unknown_type = [(p, d) for p, d in records if d.get("document_type") == UNKNOWN]
    if unknown_type:
        party_counter = Counter(d.get("issuing_party", UNKNOWN) for _, d in unknown_type)
        print(f"\n  Top issuing parties with unknown document_type:")
        for party, count in party_counter.most_common(10):
            print(f"    {party:<40} {count:>4}")

    unknown_party = [(p, d) for p, d in records if d.get("issuing_party") == UNKNOWN]
    if unknown_party:
        type_counter = Counter(d.get("document_type", UNKNOWN) for _, d in unknown_party)
        print(f"\n  Top document types with unknown issuing_party:")
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

    bucket_counts = [(label, sum(1 for c in confidences if lo <= c < hi)) for lo, hi, label in CONFIDENCE_BUCKETS]
    max_count = max(c for _, c in bucket_counts) if bucket_counts else 1
    print(f"\n  {'Bucket':<10} {'Count':>6}  {'Rate':>6}  Distribution")
    print(f"  {'-' * 10} {'-' * 6}  {'-' * 6}  {'-' * 30}")
    for label, count in bucket_counts:
        print(f"  {label:<10} {count:>6}  {_pct(count, len(confidences))}  {_bar(count, max_count, 25)}")

    if low_confidence:
        low_confidence.sort(key=lambda x: x[1])
        print(f"\n  Files with confidence < 0.6 ({len(low_confidence)}):")
        for name, conf in low_confidence[:20]:
            print(f"    {conf:.2f}  {name}")
        if len(low_confidence) > 20:
            print(f"    ... and {len(low_confidence) - 20} more")


def _section_missing_fields(records, total):
    _header("3. Missing Fields")
    print(f"\n  {'Field':<20} {'Missing':>7}  {'Rate':>6}")
    print(f"  {'-' * 20} {'-' * 7}  {'-' * 6}")
    for field in CRITICAL_FIELDS:
        missing = sum(1 for _, d in records if d.get(field) is None)
        print(f"  {field:<20} {missing:>7}  {_pct(missing, total)}")


def _section_distribution(records, total, section_num, title, field):
    _header(f"{section_num}. {title}")
    counter = Counter(d.get(field, "(null)") for _, d in records)
    max_count = counter.most_common(1)[0][1] if counter else 1
    print(f"\n  {'Value':<35} {'Count':>6}  {'Rate':>6}  Distribution")
    print(f"  {'-' * 35} {'-' * 6}  {'-' * 6}  {'-' * 25}")
    for val, count in counter.most_common():
        print(f"  {val:<35} {count:>6}  {_pct(count, total)}  {_bar(count, max_count, 20)}")

    if field == "issuing_party":
        lower_map = defaultdict(list)
        for party in counter:
            lower_map[party.lower()].append(party)
        collisions = {k: v for k, v in lower_map.items() if len(v) > 1}
        if collisions:
            print(f"\n  Potential duplicates (case-insensitive collisions):")
            for _, variants in collisions.items():
                counts = ", ".join(f"{v} ({counter[v]})" for v in variants)
                print(f"    {counts}")


def _section_dates(records):
    _header("6. Date Analysis")
    dates = []
    suspicious = []
    for path, data in records:
        date_str = data.get("date_issued")
        if not date_str or date_str == UNKNOWN:
            continue
        dates.append(date_str)
        try:
            year = int(date_str[:4])
            if year > 2026:
                suspicious.append((path.name, date_str, "future"))
            elif year < 2000:
                suspicious.append((path.name, date_str, "pre-2000"))
        except (ValueError, IndexError):
            suspicious.append((path.name, date_str, "unparseable"))

    if not dates:
        print("\n  No date_issued values found.")
        return

    dates.sort()
    print(f"\n  Date range: {dates[0]} to {dates[-1]}")
    print(f"  Total with dates: {len(dates)}")

    year_counter = Counter()
    for d in dates:
        try:
            year_counter[d[:4]] += 1
        except (IndexError, ValueError):
            pass
    print(f"\n  {'Year':<8} {'Count':>6}")
    print(f"  {'-' * 8} {'-' * 6}")
    for year in sorted(year_counter):
        print(f"  {year:<8} {year_counter[year]:>6}")

    if suspicious:
        print(f"\n  Suspicious dates ({len(suspicious)}):")
        for name, date_str, reason in suspicious[:20]:
            print(f"    [{reason}] {date_str}  {name}")
        if len(suspicious) > 20:
            print(f"    ... and {len(suspicious) - 20} more")


def _section_hash_dedup(records):
    _header("7. Hash Dedup Check")
    content_groups = defaultdict(list)
    for path, data in records:
        h = data.get("hash_content")
        if h:
            content_groups[h].append(path.name)

    unique_hashes = len(content_groups)
    with_hash = sum(len(v) for v in content_groups.values())
    dupe_groups = {h: names for h, names in content_groups.items() if len(names) > 1}

    print(f"\n  Files with hash_content: {with_hash}")
    print(f"  Unique content hashes:  {unique_hashes}")
    print(f"  Duplicate groups:       {len(dupe_groups)}")

    if dupe_groups:
        print(f"\n  Duplicate hash_content groups:")
        for h, names in sorted(dupe_groups.items(), key=lambda x: -len(x[1])):
            print(f"\n    {h} ({len(names)} files):")
            for name in names[:5]:
                print(f"      {name}")
            if len(names) > 5:
                print(f"      ... and {len(names) - 5} more")


def _section_amounts(records):
    _header("8. Amount/Currency Analysis")
    currency_counter = Counter()
    issues = []
    for path, data in records:
        amount = data.get("total_amount")
        currency = data.get("total_amount_currency")
        if amount is not None and currency:
            currency_counter[currency] += 1
        if amount is not None and not currency:
            issues.append((path.name, f"amount={amount} but no currency"))
        if amount is None and currency:
            issues.append((path.name, f"currency={currency} but no amount"))
        if amount is not None and amount < 0:
            issues.append((path.name, f"negative amount: {amount}"))
        if amount is not None and amount > 100_000:
            issues.append((path.name, f"high amount: {amount} {currency or ''}"))

    if currency_counter:
        print(f"\n  {'Currency':<10} {'Count':>6}")
        print(f"  {'-' * 10} {'-' * 6}")
        for curr, count in currency_counter.most_common():
            print(f"  {curr:<10} {count:>6}")
    else:
        print("\n  No amount/currency data found.")

    has_amount = sum(1 for _, d in records if d.get("total_amount") is not None)
    print(f"\n  Files with total_amount: {has_amount}/{len(records)}")

    if issues:
        print(f"\n  Issues ({len(issues)}):")
        for name, issue in issues[:20]:
            print(f"    {name}: {issue}")
        if len(issues) > 20:
            print(f"    ... and {len(issues) - 20} more")


def _section_qr(records):
    _header("9. QR Code Coverage")
    with_qr = with_sub = 0
    qr_types = Counter()
    sub_counts = []
    for _, data in records:
        qr = data.get("qrcode")
        if qr:
            with_qr += 1
            qr_types[qr.get("qr_type", "unknown")] += 1
        subs = data.get("sub_documents")
        if subs:
            with_sub += 1
            sub_counts.append(len(subs))

    total = len(records)
    print(f"\n  Files with QR code:      {with_qr:>6}  {_pct(with_qr, total)}")
    print(f"  Files with sub_documents:{with_sub:>6}  {_pct(with_sub, total)}")
    if qr_types:
        print(f"\n  QR type distribution:")
        for qr_type, count in qr_types.most_common():
            print(f"    {qr_type:<30} {count:>4}")
    if sub_counts:
        total_subs = sum(sub_counts)
        print(f"\n  Total sub-documents: {total_subs}")
        print(f"  Sub-documents per file: min={min(sub_counts)}, max={max(sub_counts)}, avg={total_subs / len(sub_counts):.1f}")


def _section_summary(records, total):
    _header("10. Summary")
    unknown_any = sum(1 for _, d in records if any(d.get(f) == UNKNOWN for f in ["document_type", "issuing_party", "date_issued"]))
    with_conf = [d.get("class_confidence") for _, d in records if d.get("class_confidence") is not None]
    avg_conf = sum(with_conf) / len(with_conf) if with_conf else 0
    with_qr = sum(1 for _, d in records if d.get("qrcode"))
    unique_types = len(set(d.get("document_type") for _, d in records if d.get("document_type")))
    unique_parties = len(set(d.get("issuing_party") for _, d in records if d.get("issuing_party")))
    content_hashes = set(d.get("hash_content") for _, d in records if d.get("hash_content"))

    metrics = [
        ("Total files", str(total)),
        ("Unique content hashes", str(len(content_hashes))),
        ("$UNKNOWN$ rate", _pct(unknown_any, total).strip()),
        ("Avg confidence", f"{avg_conf:.3f}"),
        ("QR coverage", _pct(with_qr, total).strip()),
        ("Unique document types", str(unique_types)),
        ("Unique issuing parties", str(unique_parties)),
    ]
    print()
    for label, value in metrics:
        print(f"  {label:<30} {value:>10}")


def _task_audit(processed_path: Path, verify_hashes: bool = False) -> None:
    """Run extraction quality audit and print report to stdout."""
    print(f"Loading metadata from: {processed_path}")
    records = _load_all_metadata(processed_path)
    total = len(records)
    print(f"Loaded {total} metadata files")

    if not records:
        print("No metadata files found.")
        return

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

    if verify_hashes:
        _header("11. Hash Verification")
        validate_metadata(processed_path)

    print()
