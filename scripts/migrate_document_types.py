#!/usr/bin/env python3
"""Migrate document_type canonical values to namespace-prefixed taxonomy.

Usage:
    python scripts/migrate_document_types.py <directory> [--dry-run]

This script updates the `document_type` field in all sidecar JSON files,
applying the old→new canonical mapping. Fields like `document_type_raw`
and `document_type_key` are left unchanged (they store raw extraction data).
"""

import json
import sys
from pathlib import Path

# Old canonical → New canonical mapping
MIGRATION_MAP = {
    "bank-credit-card-statement": "bank-card",
    "bank-fees-statement": "bank-note",
    "bank-iban-statement": "bank-iban",
    "bank-investment-statement": "bank-investment",
    "bank-stock-purchase": "bank-stock-buy",
    "bank-stock-sale": "bank-stock-sell",
    "car-insurance": "insurance-auto",
    "circulation-declaration": "tax-iuc",
    "credit-note": "invoice-credit",
    "income-statement": "finance-income",
    "insurance-premium-notice": "insurance-notice",
    "investment-declaration": "tax-investment",
    "labour-compensation-fund-payment-slip": "payroll-fund",
    "notification": "notice",
    "payment-reference": "receipt-reference",
    "proof-of-delivery": "receipt-delivery",
    "purchase-order": "invoice-order",
    "request-for-information": "notice-request",
    "salary-slip": "payroll-salary",
    "signup-contract": "contract-signup",
    "social-security-extract": "payroll-social",
    "tax-irc-payment-slip": "tax-irc-payment",
    "tax-irc-settlement-statement": "tax-irc",
    "tax-irs-irc-withholding-declaration": "tax-withholding",
    "tax-irs-monthly-statement": "tax-irs",
    "tax-iuc-payment-slip": "tax-iuc",
    "tax-vat-periodic-declaration": "tax-vat",
    "transaction-note": "bank-note",
    "trial-balance": "finance-balance",
    "vacation-pay-slip": "payroll-vacation",
    "W-8BEN-E": "tax-form",
}

# Types that stay the same (no migration needed):
# bank-statement, bank-transfer, contract, invoice, invoice-receipt,
# other, receipt, tax-declaration, $UNKNOWN$


def migrate_file(json_path: Path, dry_run: bool = False) -> tuple[bool, str]:
    """Migrate a single JSON file. Returns (changed, message)."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    old_value = data.get("document_type")
    if old_value is None:
        return False, "no document_type field"

    new_value = MIGRATION_MAP.get(old_value)
    if new_value is None:
        return False, f"no migration for '{old_value}'"

    if dry_run:
        return True, f"would change '{old_value}' → '{new_value}'"

    data["document_type"] = new_value
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    return True, f"changed '{old_value}' → '{new_value}'"


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
