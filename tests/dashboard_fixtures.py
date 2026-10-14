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
