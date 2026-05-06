import tempfile
import unittest
from pathlib import Path

import fitz

from papertrail.commands.reconcile import (
    CandidateLineItem,
    Transaction,
    _extract_bpi_fee_invoice_line_items,
    _line_item_matches_transaction_context,
)


class ReconcileLineItemTests(unittest.TestCase):
    def test_extracts_bpi_fee_invoice_line_items(self):
        lines = [
            "FACTURA",
            "COMISSÕES DE CONTA / JUROS",
            "TÍTULOS",
            "Data de Emissão: 30-04-2026",
            "MANUTENÇÃO DE CONTA VALOR NEGÓCIOS MAR 2026",
            "COMISSÃO CORRETAGEM",
            "COMISSÃO DEPÓSITO E REGISTO VALORES MOBILIÁRIOS",
            "08/04",
            "01/04",
            "08/04",
            "01/04",
            "01/04",
            "01/04",
            "01/04",
            "8,31",
            "16,81",
            "11,06",
            "7,99",
            "16,16",
            "8,99",
            "TOTAL A DÉBITO",
            "11,06",
            "-5 710,24",
            "5 725,84",
            "EUR",
            "USD",
            "COMISSÃO DEPÓSITO E REGISTO VALORES MOBILIÁRIOS 1ºTRIMESTRE 2026",
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "bpi.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), "\n".join(lines), fontsize=10)
            doc.save(path)
            doc.close()

            items = _extract_bpi_fee_invoice_line_items(
                path,
                {
                    "document_type": "invoice",
                    "issuing_party": "BPI",
                    "document_title": "Comissões de conta e títulos",
                },
            )

        by_category = {(item.category, item.amount) for item in items}
        self.assertIn(("bank-fee-maintenance", 7.99), by_category)
        self.assertIn(("bank-fee-stamp-duty", 0.32), by_category)
        self.assertIn(("bank-custody-fee", 11.06), by_category)

    def test_line_item_match_rejects_wrong_month_when_period_is_present(self):
        txn = Transaction(
            row_number=24,
            date_posting="2026-04-08",
            date_value="2026-04-08",
            description="MANUTENCAO DE CONTA VALOR NEGOCIOS MAR 2026",
            amount=-7.99,
            currency="EUR",
            notes="",
            treated="",
        )

        self.assertFalse(
            _line_item_matches_transaction_context(
                txn,
                CandidateLineItem(
                    category="bank-fee-maintenance",
                    amount=7.99,
                    label="MANUTENÇÃO DE CONTA VALOR NEGÓCIOS FEV 2026",
                ),
            )
        )
        self.assertTrue(
            _line_item_matches_transaction_context(
                txn,
                CandidateLineItem(
                    category="bank-fee-maintenance",
                    amount=7.99,
                    label="MANUTENÇÃO DE CONTA VALOR NEGÓCIOS MAR 2026",
                ),
            )
        )


if __name__ == "__main__":
    unittest.main()
