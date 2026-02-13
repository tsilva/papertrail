#!/usr/bin/env python3
"""Migrate QR D:FR documents from document_type "receipt" to "invoice-receipt".

Usage:
    python scripts/migrate_fr_to_invoice_receipt.py <directory> [--dry-run]

Portuguese QR code type D:FR means "Fatura/Recibo" (Invoice/Receipt) — a hybrid
document. The previous mapping incorrectly mapped FR → "receipt", losing the
invoice nature. This script fixes existing documents.

Criteria: qrcode.raw_content contains "*D:FR*" AND document_type == "receipt".
"""

import json
import sys
from datetime import date
from pathlib import Path


def migrate_file(json_path: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Migrate a single JSON file. Returns (changed, message)."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Must have document_type == "receipt"
    if data.get("document_type") != "receipt":
        return False, "not a receipt"

    # Must have QR code with D:FR in raw_content
    qrcode = data.get("qrcode")
    if not qrcode or not isinstance(qrcode, dict):
        return False, "no qrcode data"

    raw_content = qrcode.get("raw_content", "")
    if "*D:FR*" not in raw_content:
        return False, "not a D:FR QR code"

    if dry_run:
        return True, f"would change 'receipt' -> 'invoice-receipt'"

    data["document_type"] = "invoice-receipt"
    data["date_updated"] = date.today().isoformat()

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return True, "changed 'receipt' -> 'invoice-receipt'"


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <directory> [--dry-run]")
        sys.exit(1)

    directory = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv

    if not directory.is_dir():
        print(f"Error: {directory} is not a directory")
        sys.exit(1)

    if dry_run:
        print("DRY RUN — no files will be modified\n")

    json_files = sorted(directory.rglob("*.json"))
    changed_count = 0
    skipped_count = 0

    for json_path in json_files:
        # Skip log directories and non-metadata files
        if "/logs/" in str(json_path):
            continue

        try:
            changed, message = migrate_file(json_path, dry_run)
            if changed:
                changed_count += 1
                print(f"  [MIGRATED] {json_path.name}: {message}")
            else:
                skipped_count += 1
        except Exception as e:
            print(f"  [ERROR] {json_path.name}: {e}")

    print(f"\nSummary: {changed_count} migrated, {skipped_count} skipped")


if __name__ == "__main__":
    main()
