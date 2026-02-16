"""One-time script to fix 17 Montepio-misclassified files.

These files have issuing_party "MONTEPIO" but issuing_party_raw shows
"Banco Comercial Português, S.A." (Millennium BCP). The LLM incorrectly
extracted Montepio's NIF (500792615) instead of BCP's (TESTBANKALPHATAX), and
NIF enrichment auto-accepted the wrong issuer.

Usage:
    python scripts/fix_montepio.py <processed_path> [--dry-run]
"""

import json
import sys
from datetime import date
from pathlib import Path


def main():
    if len(sys.argv) < 2:
        print("Usage: python scripts/fix_montepio.py <processed_path> [--dry-run]")
        sys.exit(1)

    processed_path = Path(sys.argv[1])
    dry_run = "--dry-run" in sys.argv

    if not processed_path.is_dir():
        print(f"Error: {processed_path} is not a directory")
        sys.exit(1)

    today = date.today().isoformat()
    patched = 0

    for json_path in sorted(processed_path.glob("*.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        issuing_party = (data.get("issuing_party") or "").upper()
        issuing_party_raw = data.get("issuing_party_raw") or ""

        if "MONTEPIO" not in issuing_party:
            continue
        if "Banco Comercial Portugu" not in issuing_party_raw:
            continue

        print(f"{'[DRY RUN] ' if dry_run else ''}Patching: {json_path.name}")
        print(f"  issuing_party: {data.get('issuing_party')!r} -> 'MillenniumBCP'")
        print(f"  issuer_tax_number: {data.get('issuer_tax_number')!r} -> null")
        print(f"  date_updated: {data.get('date_updated')!r} -> '{today}'")

        if not dry_run:
            data["issuing_party"] = "MillenniumBCP"
            data["issuer_tax_number"] = None
            data["date_updated"] = today
            json_path.write_text(
                json.dumps(data, indent=4, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        patched += 1

    print(f"\n{'Would patch' if dry_run else 'Patched'} {patched} files.")


if __name__ == "__main__":
    main()
