"""Entirely fictional documents for dashboard API and visual verification."""

import json
from pathlib import Path

import fitz
import openpyxl


def make_dashboard_fixture(root: Path) -> tuple[Path, Path]:
    processed, export = root / "processed", root / "export"
    processed.mkdir(parents=True)
    month = export / "2026-10"
    month.mkdir(parents=True)
    businesses = [
        ("Northline Studio", 184.5, "12", "INV-2048"),
        ("Cedar Office", 76.2, "08", "INV-6615"),
        ("Harbor Mobile", 29.9, "09", "INV-7720"),
        ("Atlas Hosting", 48, "10", "INV-3187"),
        ("Skyway Travel", 62.4, "13", "TVL-7781"),
    ]
    matches = []
    for index, (name, amount, day, number) in enumerate(businesses):
        filename = f"{number.lower()}.pdf"
        pdf = fitz.open()
        page = pdf.new_page(width=595, height=842)
        page.insert_text((44, 80), name, fontsize=28, color=(0.06, 0.14, 0.28))
        page.insert_text((44, 108), "Creative work. Carefully delivered.", fontsize=10)
        page.insert_text((44, 180), "Invoice", fontsize=28)
        page.insert_text((44, 212), number, fontsize=16)
        page.insert_text(
            (350, 180),
            "Date   12 October 2026" if day == "12" else f"Date   {day} October 2026",
            fontsize=11,
        )
        page.insert_text((44, 295), "Description", fontsize=12)
        page.insert_text((440, 295), "Amount", fontsize=12)
        page.draw_line((44, 308), (550, 308), color=(0.6, 0.7, 0.8))
        page.insert_text(
            (44, 341), "Brand assets" if index == 0 else "Monthly services", fontsize=13
        )
        page.insert_text((440, 341), f"EUR {amount / 1.23:.2f}", fontsize=13)
        page.draw_line((44, 368), (550, 368), color=(0.8, 0.85, 0.9))
        page.insert_text((350, 430), "Subtotal", fontsize=12)
        page.insert_text((440, 430), f"EUR {amount / 1.23:.2f}", fontsize=12)
        page.insert_text((350, 465), "VAT (23%)", fontsize=12)
        page.insert_text((440, 465), f"EUR {amount - amount / 1.23:.2f}", fontsize=12)
        page.draw_line((345, 483), (550, 483), color=(0.6, 0.7, 0.8))
        page.insert_text((350, 518), "Total", fontsize=15)
        page.insert_text((440, 518), f"EUR {amount:.2f}", fontsize=15)
        page.insert_text((44, 730), "Thank you for your business.", fontsize=11)
        pdf.save(processed / filename)
        pdf.close()
        metadata = {
            "date_issued": f"2026-10-{day}",
            "document_type": "invoice",
            "document_type_raw": "Invoice",
            "document_title": f"Invoice {number}",
            "issuing_party": name.lower().replace(" ", "-"),
            "issuing_party_raw": name,
            "total_amount": amount,
            "total_amount_currency": "EUR",
            "hash_file": f"file-{index}",
            "hash_content": f"content-{index}",
        }
        (processed / filename).with_suffix(".json").write_text(json.dumps(metadata))
        (month / filename).write_bytes((processed / filename).read_bytes())
        (month / filename).with_suffix(".json").write_text(json.dumps(metadata))
        matches.append(
            {
                "row": index + 1,
                "date": f"2026-10-{day}",
                "description": name.upper(),
                "amount": -amount,
                "currency": "EUR",
                "files": [filename],
                "method": "exact",
                "confidence": 1,
                "warnings": [],
                "errors": [],
            }
        )
    book = openpyxl.Workbook()
    book.active.append(["Date", "Transaction", "Amount"])
    for row in matches:
        book.active.append([row["date"], row["description"], row["amount"]])
    book.save(month / "statement.xlsx")
    (month / "statement.json").write_text(
        json.dumps(
            {
                "date_issued": "2026-10-13",
                "document_type": "bank-statement",
                "document_type_raw": "Bank statement",
                "document_title": "October bank statement",
                "issuing_party": "rivermere-bank",
                "issuing_party_raw": "Rivermere Bank",
                "hash_file": "statement-file",
                "hash_content": "statement-content",
                "bank_statement": {"period_start": "2026-10-01", "period_end": "2026-10-13"},
            }
        )
    )
    (month / "statement.reconciliation.json").write_text(
        json.dumps(
            {
                "matches": matches,
                "unmatched": [
                    {
                        "row": 6,
                        "date": "2026-10-08",
                        "description": "PURCHASE",
                        "amount": -18,
                        "currency": "EUR",
                    }
                ],
            }
        )
    )
    return processed, export


def make_monthly_dashboard_fixture(root: Path) -> tuple[Path, Path]:
    """Two banks, an unreconciled statement, orphan evidence and another month."""
    import shutil

    processed, export = make_dashboard_fixture(root)
    month = export / "2026-10"
    meta_path = month / "statement.json"
    meta = json.loads(meta_path.read_text())
    meta["bank_statement"]["account_number"] = "RIVER-001"
    meta_path.write_text(json.dumps(meta))
    recon_path = month / "statement.reconciliation.json"
    recon = json.loads(recon_path.read_text())
    orphan = recon["matches"].pop()
    recon["matches"][3].update(
        method="llm",
        confidence=0.6,
        transaction_category="hosting",
        warnings=["Amount needs checking"],
        reasoning="Invoice total differs from bank evidence.",
    )
    recon["unmatched_files"] = [
        {
            "file": orphan["files"][0],
            "date_issued": orphan["date"],
            "total_amount": abs(orphan["amount"]),
            "currency": "EUR",
        }
    ]
    recon_path.write_text(json.dumps(recon))
    shutil.copyfile(month / "statement.xlsx", month / "coast.xlsx")
    coast = {
        **meta,
        "issuing_party_raw": "Coast Bank",
        "issuing_party": "coast-bank",
        "hash_file": "coast-statement",
        "hash_content": "coast-statement",
        "document_title": "Coast October statement",
        "bank_statement": {**meta["bank_statement"], "account_number": "COAST-002"},
    }
    (month / "coast.json").write_text(json.dumps(coast))
    (month / "coast.reconciliation.json").write_text(
        json.dumps(
            {
                "matches": [
                    {
                        **recon["matches"][0],
                        "amount": 184.5,
                        "transaction_category": "refund",
                        "reasoning": "Refund supported by the original invoice.",
                    }
                ],
                "unmatched": [],
            }
        )
    )
    shutil.copyfile(month / "statement.xlsx", month / "waiting.xlsx")
    (month / "waiting.json").write_text(
        json.dumps(
            {
                **coast,
                "hash_file": "waiting-statement",
                "hash_content": "waiting-statement",
                "document_title": "Coast savings statement",
                "bank_statement": {
                    **coast["bank_statement"],
                    "account_number": "COAST-003",
                    "transaction_count": 7,
                },
            }
        )
    )
    previous = export / "2026-09"
    shutil.copytree(month, previous)
    for sidecar in previous.glob("*.json"):
        if sidecar.name.endswith(".reconciliation.json"):
            data = json.loads(sidecar.read_text())
            for row in data.get("matches", []) + data.get("unmatched", []):
                row["date"] = row["date"].replace("2026-10", "2026-09")
        else:
            data = json.loads(sidecar.read_text())
            data["date_issued"] = data["date_issued"].replace("2026-10", "2026-09")
            data["hash_file"] = "september-" + data["hash_file"]
            data["hash_content"] = "september-" + data["hash_content"]
            if "bank_statement" in data:
                for key in ("period_start", "period_end"):
                    data["bank_statement"][key] = data["bank_statement"][key].replace(
                        "2026-10", "2026-09"
                    )
        sidecar.write_text(json.dumps(data))
    return processed, export
