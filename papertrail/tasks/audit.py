"""Extraction quality audit — comprehensive report on metadata health.

Read-only — no file modifications.
"""

from collections import Counter, defaultdict
from pathlib import Path

from papertrail.metadata import iter_json_files

UNKNOWN = "$UNKNOWN$"

CRITICAL_FIELDS = [
    "hash_content", "hash_file", "hash_text", "date_issued",
    "document_type", "issuing_party", "document_title",
    "class_confidence", "page_count", "file_size_kb",
]

CONFIDENCE_BUCKETS = [
    (0.0, 0.5, "<0.5"),
    (0.5, 0.6, "0.5-0.6"),
    (0.6, 0.7, "0.6-0.7"),
    (0.7, 0.8, "0.7-0.8"),
    (0.8, 0.9, "0.8-0.9"),
    (0.9, 1.01, "0.9-1.0"),
]


def _bar(count: int, total: int, width: int = 30) -> str:
    if total == 0:
        return ""
    filled = round(count / total * width)
    return "\u2588" * filled + "\u2591" * (width - filled)


def _pct(count: int, total: int) -> str:
    if total == 0:
        return "  0.0%"
    return f"{count / total * 100:5.1f}%"


def _header(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def _is_sidecar(path: Path) -> bool:
    name = path.name
    return name.endswith(".reconciliation.json") or name.endswith(".embeddings.json")


def _load_all_metadata(processed_path: Path) -> list[tuple[Path, dict]]:
    records = []
    for json_path, data in iter_json_files(processed_path):
        if _is_sidecar(json_path):
            continue
        records.append((json_path, data))
    return records


def _section_unknown(records: list[tuple[Path, dict]]) -> None:
    _header("1. $UNKNOWN$ Analysis")
    total = len(records)

    unknown_fields = ["document_type", "issuing_party", "date_issued"]
    field_counts = {}
    for field in unknown_fields:
        field_counts[field] = sum(1 for _, d in records if d.get(field) == UNKNOWN)

    combined = sum(1 for _, d in records if any(d.get(f) == UNKNOWN for f in unknown_fields))

    print(f"\n  {'Field':<20} {'Count':>6}  {'Rate':>6}")
    print(f"  {'-' * 20} {'-' * 6}  {'-' * 6}")
    for field in unknown_fields:
        c = field_counts[field]
        print(f"  {field:<20} {c:>6}  {_pct(c, total)}")
    print(f"  {'(any field)':<20} {combined:>6}  {_pct(combined, total)}")

    # Top issuing parties with unknown document_type
    unknown_type = [(p, d) for p, d in records if d.get("document_type") == UNKNOWN]
    if unknown_type:
        party_counter = Counter(d.get("issuing_party", UNKNOWN) for _, d in unknown_type)
        print(f"\n  Top issuing parties with unknown document_type:")
        for party, count in party_counter.most_common(10):
            print(f"    {party:<40} {count:>4}")

    # Top types with unknown issuing_party
    unknown_party = [(p, d) for p, d in records if d.get("issuing_party") == UNKNOWN]
    if unknown_party:
        type_counter = Counter(d.get("document_type", UNKNOWN) for _, d in unknown_party)
        print(f"\n  Top document types with unknown issuing_party:")
        for dtype, count in type_counter.most_common(10):
            print(f"    {dtype:<40} {count:>4}")


def _section_confidence(records: list[tuple[Path, dict]]) -> None:
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

    bucket_counts = []
    for lo, hi, label in CONFIDENCE_BUCKETS:
        count = sum(1 for c in confidences if lo <= c < hi)
        bucket_counts.append((label, count))

    max_count = max(c for _, c in bucket_counts) if bucket_counts else 1
    print(f"\n  {'Bucket':<10} {'Count':>6}  {'Rate':>6}  Distribution")
    print(f"  {'-' * 10} {'-' * 6}  {'-' * 6}  {'-' * 30}")
    for label, count in bucket_counts:
        bar = _bar(count, max_count, 25)
        print(f"  {label:<10} {count:>6}  {_pct(count, len(confidences))}  {bar}")

    if low_confidence:
        low_confidence.sort(key=lambda x: x[1])
        print(f"\n  Files with confidence < 0.6 ({len(low_confidence)}):")
        for name, conf in low_confidence[:20]:
            print(f"    {conf:.2f}  {name}")
        if len(low_confidence) > 20:
            print(f"    ... and {len(low_confidence) - 20} more")


def _section_missing_fields(records: list[tuple[Path, dict]]) -> None:
    _header("3. Missing Fields")
    total = len(records)

    print(f"\n  {'Field':<20} {'Missing':>7}  {'Rate':>6}")
    print(f"  {'-' * 20} {'-' * 7}  {'-' * 6}")
    for field in CRITICAL_FIELDS:
        missing = sum(1 for _, d in records if d.get(field) is None)
        print(f"  {field:<20} {missing:>7}  {_pct(missing, total)}")


def _section_document_types(records: list[tuple[Path, dict]]) -> None:
    _header("4. Document Type Distribution")
    total = len(records)

    type_counter = Counter(d.get("document_type", "(null)") for _, d in records)
    max_count = type_counter.most_common(1)[0][1] if type_counter else 1

    print(f"\n  {'Type':<35} {'Count':>6}  {'Rate':>6}  Distribution")
    print(f"  {'-' * 35} {'-' * 6}  {'-' * 6}  {'-' * 25}")
    for dtype, count in type_counter.most_common():
        bar = _bar(count, max_count, 20)
        print(f"  {dtype:<35} {count:>6}  {_pct(count, total)}  {bar}")


def _section_issuing_parties(records: list[tuple[Path, dict]]) -> None:
    _header("5. Issuing Party Distribution")
    total = len(records)

    party_counter = Counter(d.get("issuing_party", "(null)") for _, d in records)
    max_count = party_counter.most_common(1)[0][1] if party_counter else 1

    print(f"\n  {'Party':<35} {'Count':>6}  {'Rate':>6}  Distribution")
    print(f"  {'-' * 35} {'-' * 6}  {'-' * 6}  {'-' * 25}")
    for party, count in party_counter.most_common():
        bar = _bar(count, max_count, 20)
        print(f"  {party:<35} {count:>6}  {_pct(count, total)}  {bar}")

    # Check for case-insensitive duplicates
    lower_map = defaultdict(list)
    for party in party_counter:
        lower_map[party.lower()].append(party)

    collisions = {k: v for k, v in lower_map.items() if len(v) > 1}
    if collisions:
        print(f"\n  Potential duplicates (case-insensitive collisions):")
        for _, variants in collisions.items():
            counts = ", ".join(f"{v} ({party_counter[v]})" for v in variants)
            print(f"    {counts}")


def _section_dates(records: list[tuple[Path, dict]]) -> None:
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


def _section_hash_dedup(records: list[tuple[Path, dict]]) -> None:
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


def _section_amounts(records: list[tuple[Path, dict]]) -> None:
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


def _section_qr(records: list[tuple[Path, dict]]) -> None:
    _header("9. QR Code Coverage")

    with_qr = 0
    with_sub = 0
    qr_types = Counter()
    sub_counts = []

    for _, data in records:
        qr = data.get("qrcode")
        if qr:
            with_qr += 1
            qr_type = qr.get("qr_type", "unknown")
            qr_types[qr_type] += 1

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


def _section_summary(records: list[tuple[Path, dict]]) -> None:
    _header("10. Summary")
    total = len(records)

    unknown_any = sum(
        1 for _, d in records
        if any(d.get(f) == UNKNOWN for f in ["document_type", "issuing_party", "date_issued"])
    )
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


def task_audit(processed_path: Path) -> None:
    """Run extraction quality audit and print report to stdout."""
    print(f"Loading metadata from: {processed_path}")
    records = _load_all_metadata(processed_path)
    print(f"Loaded {len(records)} metadata files")

    if not records:
        print("No metadata files found.")
        return

    _section_unknown(records)
    _section_confidence(records)
    _section_missing_fields(records)
    _section_document_types(records)
    _section_issuing_parties(records)
    _section_dates(records)
    _section_hash_dedup(records)
    _section_amounts(records)
    _section_qr(records)
    _section_summary(records)
    print()
