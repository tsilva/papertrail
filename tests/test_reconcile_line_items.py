import tempfile
import unittest
from pathlib import Path

import fitz

from papertrail.commands.reconcile import (
    CandidateLineItem,
    Transaction,
    _extract_bpi_fee_invoice_line_items,
    _extract_bpi_stock_invoice_line_items,
    _extract_millennium_fee_invoice_line_items,
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

    def test_extracts_millennium_fee_invoice_receipt_line_items(self):
        lines = [
            "Fatura-Recibo",
            "Data emissão: 2026-03-04",
            "Operação: MAN. CTA PACOTE M EMPRESA",
            "Dados de Operação",
            "Data do Movimento",
            "2026-03-04",
            "Dados de Faturação",
            "Comissão referente a  02/2026          (1)",
            "15,00",
            "EUR",
            "Imp. Selo art.17.3.4 da Tab. Geral - 4%",
            "0,60",
            "EUR",
            "Total de Faturação",
            "15,60",
            "EUR",
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "millennium.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), "\n".join(lines), fontsize=10)
            doc.save(path)
            doc.close()

            items = _extract_millennium_fee_invoice_line_items(
                path,
                {
                    "date_issued": "2026-03-04",
                    "document_type": "invoice-receipt",
                    "issuing_party": "MillenniumBCP",
                    "document_title": "MAN. CTA PACOTE M EMPRESA",
                },
            )

        by_category = {(item.category, item.amount, item.date_issued) for item in items}
        self.assertIn(("bank-fee-maintenance", 15.00, "2026-03-04"), by_category)
        self.assertIn(("bank-fee-stamp-duty", 0.60, "2026-03-04"), by_category)

    def test_extracts_bpi_stock_sale_invoice_line_items(self):
        lines = [
            "FACTURA",
            "Data de Emissão: 30-04-2026",
            "TÍTULOS",
            "01/04 01/04",
            "DESCRIÇÃO",
            "COMISSÃO CORRETAGEM",
            "01/04",
            "OPERAÇÃO BOLSA",
            "12 300,00 USD",
            "01/04",
            "TOTAL A CRÉDITO",
            "12 280,81 USD",
            "VENDA DE 100,0000 ACÇÕES STRATEGY INC(XNGS)",
            "AO PREÇO DE: 123,000000 USD NA SESSÃO DE BOLSA: 31-03-2026 DA NASDAQ - ALL MARKETS",
            "Nº ORDEM: V7538895",
        ]
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "bpi.pdf"
            doc = fitz.open()
            page = doc.new_page()
            page.insert_text((72, 72), "\n".join(lines), fontsize=10)
            doc.save(path)
            doc.close()

            items = _extract_bpi_stock_invoice_line_items(
                path,
                {
                    "date_issued": "2026-04-30",
                    "document_type": "invoice",
                    "issuing_party": "BPI",
                    "document_title": "Comissões de conta e títulos",
                },
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].category, "stock-sale-bpi")
        self.assertEqual(items[0].amount, 12280.81)
        self.assertEqual(items[0].amount_currency, "USD")
        self.assertEqual(items[0].date_issued, "2026-04-01")
        self.assertEqual(items[0].document_type, "bank-stock-sell")
        self.assertFalse(items[0].amount_match_required)

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
