import json
import tempfile
import unittest
from pathlib import Path

from papertrail.bank_statement import classify_bank_statement
from papertrail.commands import reconcile
from papertrail.hashing import hash_file_fast
from papertrail.models import DocumentMetadata
from papertrail.repository import DocumentRepository
from tests.support import create_bpi_statement, create_millennium_statement, create_pdf, make_test_runtime


class ReconciliationEvidenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.runtime = make_test_runtime(self.root)
        self.repository = DocumentRepository(self.runtime)
        self.export = self.runtime.paths.export

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_evidence_supplier_payment_requires_bank_anchor_and_supplier_evidence(self):
        statement = self._millennium_statement(
            [
                {
                    "date_posting": "15/04/2026",
                    "date_value": "15/04/2026",
                    "description": "STORE PAYMENT",
                    "amount": -12.34,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ]
        )
        bank_note = self.export / "BNC_bank-note.pdf"
        receipt = self.export / "CMP_receipt.pdf"
        create_pdf(bank_note, ["Bank note"])
        create_pdf(receipt, ["Receipt"])
        self.repository.save_document(
            bank_note,
            self._metadata("bank1", "bank1", document_type="bank-note", issuing_party="MillenniumBCP"),
        )
        self.repository.save_document(
            receipt,
            self._metadata("recv1", "recv1", document_type="receipt", issuing_party="Vendor"),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        row = self._only_match(statement)
        self.assertEqual(row["errors"], [])
        self.assertEqual(set(row["files"]), {bank_note.name, receipt.name})

    def test_evidence_links_via_verde_shared_monthly_receipt(self):
        statement = self._millennium_statement(
            [
                {
                    "date_posting": "30/04/2026",
                    "date_value": "30/04/2026",
                    "description": "MDB TESTREF PAG BX VAL-SHAREDTOLL MOV 1",
                    "amount": -1.90,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ]
        )
        card = self.export / "BNC_shared-toll-card.pdf"
        receipt = self.export / "CMP_shared-toll-monthly.pdf"
        create_pdf(card, ["Shared Toll card"])
        create_pdf(receipt, ["Pagamentos de Serviços Shared Toll"])
        self.repository.save_document(
            card,
            self._metadata("card1", "card1", document_type="bank-card-transaction", issuing_party="Shared Toll", total_amount=1.90),
        )
        self.repository.save_document(
            receipt,
            self._metadata(
                "via1",
                "via1",
                date_issued="2026-04-30",
                document_type="invoice-receipt",
                issuing_party="Shared Toll",
                issuer_tax_number="TESTSHAREDTOLL",
                document_title="Pagamentos de Serviços Shared Toll",
                total_amount=32.70,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        row = self._only_match(statement)
        self.assertEqual(row["errors"], [])
        self.assertEqual(set(row["files"]), {card.name, receipt.name})

    def test_evidence_links_via_verde_shared_receipt_to_multiple_movements_without_bank_anchors(self):
        statement = self._millennium_statement(
            [
                {
                    "date_posting": "30/04/2026",
                    "date_value": "30/04/2026",
                    "description": "MDB TESTREF PAG BX VAL-SHAREDTOLL MOV 10",
                    "amount": -9.35,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
                {
                    "date_posting": "22/04/2026",
                    "date_value": "22/04/2026",
                    "description": "MDB TESTREF PAG BX VAL-SHAREDTOLL MOV  9",
                    "amount": -4.90,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
            ]
        )
        receipt = self.export / "CMP_shared-toll-monthly.pdf"
        create_pdf(receipt, ["Pagamentos de Serviços Shared Toll"])
        self.repository.save_document(
            receipt,
            self._metadata(
                "via1",
                "via1",
                date_issued="2026-04-30",
                document_type="receipt",
                document_type_raw="Extrato/Recibo",
                issuing_party="Shared Toll",
                issuer_tax_number="TESTSHAREDTOLL",
                document_title="Pagamentos de Serviços Shared Toll",
                total_amount=26.51,
                sub_documents=[
                    {
                        "date_issued": "2026-04-30",
                        "document_type": "invoice",
                        "issuing_party": "Infraestruturas de Portugal",
                        "total_amount": 0.20,
                        "total_amount_currency": "EUR",
                    },
                    {
                        "date_issued": "2026-04-30",
                        "document_type": "invoice",
                        "issuing_party": "Brisa",
                        "total_amount": 8.30,
                        "total_amount_currency": "EUR",
                    },
                ],
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        data = json.loads(statement.with_suffix(".reconciliation.json").read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 2)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["summary"]["unmatched"], 0)
        self.assertEqual(data["unmatched_files"], [])
        files_by_row = {match["row"]: match["files"] for match in data["matches"]}
        self.assertEqual(files_by_row[9], [receipt.name])
        self.assertEqual(files_by_row[10], [receipt.name])

    def test_evidence_allows_via_verde_bank_anchor_without_no_qr_summary(self):
        statement = self._millennium_statement(
            [
                {
                    "date_posting": "02/03/2026",
                    "date_value": "02/03/2026",
                    "description": "MDB TESTREF PAG BX VAL-SHAREDTOLL MOV 16",
                    "amount": -4.00,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ]
        )
        bank_note = self.export / "BNC_shared-toll-bank-note.pdf"
        create_pdf(bank_note, ["Shared Toll bank note"])
        self.repository.save_document(
            bank_note,
            self._metadata(
                "card1",
                "card1",
                date_issued="2026-03-02",
                document_type="bank-note",
                issuing_party="Shared Toll",
                total_amount=4.00,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        row = self._only_match(statement)
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["files"], [bank_note.name])

    def test_evidence_prefers_same_month_supplier_evidence_for_recurring_amounts(self):
        statement = self._millennium_statement(
            [
                {
                    "date_posting": "17/03/2026",
                    "date_value": "17/03/2026",
                    "description": "MDB1717 MDB Google O     1.99EUR",
                    "amount": -1.99,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ]
        )
        card = self.export / "BNC_google-card.pdf"
        march_invoice = self.export / "CMP_google-march.pdf"
        april_invoice = self.export / "CMP_google-april.pdf"
        create_pdf(card, ["Google card"])
        create_pdf(march_invoice, ["Google March"])
        create_pdf(april_invoice, ["Google April"])
        self.repository.save_document(
            card,
            self._metadata(
                "card1",
                "card1",
                date_issued="2026-03-17",
                document_type="bank-card-transaction",
                issuing_party="Google",
                total_amount=1.99,
            ),
        )
        self.repository.save_document(
            march_invoice,
            self._metadata(
                "google1",
                "google1",
                date_issued="2026-03-12",
                document_type="invoice",
                issuing_party="Google",
                total_amount=1.99,
            ),
        )
        self.repository.save_document(
            april_invoice,
            self._metadata(
                "google2",
                "google2",
                date_issued="2026-04-12",
                document_type="invoice",
                issuing_party="Google",
                total_amount=1.99,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        row = self._only_match(statement)
        self.assertEqual(row["errors"], [])
        self.assertEqual(set(row["files"]), {card.name, march_invoice.name})

    def test_evidence_uses_supplier_direct_debit_date_for_recurring_invoices(self):
        statement = self._millennium_statement(
            [
                {
                    "date_posting": "19/03/2026",
                    "date_value": "19/03/2026",
                    "description": "DD UTILITY PORTU AUTHUTILITY001    PAYREF001",
                    "amount": -86.73,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ]
        )
        bank_note = self.export / "BNC_utility-provider-bank-note.pdf"
        march_invoice = self.export / "CMP_utility-provider-march.pdf"
        april_invoice = self.export / "CMP_utility-provider-april.pdf"
        create_pdf(bank_note, ["Utility Provider bank note"])
        create_pdf(
            march_invoice,
            ["Informação do Pagamento\nNº autorização: AUTHUTILITY001\nValor: € 86,73\nDébito a partir de: 19-03-2026"],
        )
        create_pdf(
            april_invoice,
            ["Informação do Pagamento\nNº autorização: AUTHUTILITY001\nValor: € 86,73\nDébito a partir de: 24-04-2026"],
        )
        self.repository.save_document(
            bank_note,
            self._metadata(
                "bank1",
                "bank1",
                date_issued="2026-03-19",
                document_type="bank-note",
                issuing_party="MillenniumBCP",
                total_amount=86.73,
            ),
        )
        self.repository.save_document(
            march_invoice,
            self._metadata(
                "utility-provider1",
                "utility-provider1",
                date_issued="2026-03-03",
                document_type="invoice",
                issuing_party="Utility Provider",
                issuer_tax_number="TESTUTILITY",
                total_amount=86.73,
            ),
        )
        self.repository.save_document(
            april_invoice,
            self._metadata(
                "utility-provider2",
                "utility-provider2",
                date_issued="2026-04-03",
                document_type="invoice",
                issuing_party="Utility Provider",
                issuer_tax_number="TESTUTILITY",
                total_amount=86.73,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        row = self._only_match(statement)
        self.assertEqual(row["errors"], [])
        self.assertEqual(set(row["files"]), {bank_note.name, march_invoice.name})

    def test_evidence_links_salary_slip_to_two_salary_transfers(self):
        statement = self._millennium_statement(
            [
                {
                    "date_posting": "31/03/2026",
                    "date_value": "31/03/2026",
                    "description": "TRF P/ Employee One",
                    "amount": -964.85,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
                {
                    "date_posting": "31/03/2026",
                    "date_value": "31/03/2026",
                    "description": "TRF P/ Employee Two",
                    "amount": -557.72,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
            ]
        )
        employee-one_bank_note = self.export / "BNC_employee-one-salary.pdf"
        employee-two_bank_note = self.export / "BNC_employee-two-salary.pdf"
        salary_slip = self.export / "DIV_salary-slip.pdf"
        create_pdf(employee-one_bank_note, ["Employee One salary bank note"])
        create_pdf(employee-two_bank_note, ["Employee Two salary bank note"])
        create_pdf(salary_slip, ["Salary slip for Employee One and Employee Two"])
        self.repository.save_document(
            employee-one_bank_note,
            self._metadata(
                "crbank1",
                "crbank1",
                date_issued="2026-03-31",
                document_type="bank-note",
                issuing_party="MillenniumBCP",
                total_amount=964.85,
            ),
        )
        self.repository.save_document(
            employee-two_bank_note,
            self._metadata(
                "tibank1",
                "tibank1",
                date_issued="2026-03-31",
                document_type="bank-note",
                issuing_party="MillenniumBCP",
                total_amount=557.72,
            ),
        )
        self.repository.save_document(
            salary_slip,
            self._metadata(
                "salary1",
                "salary1",
                date_issued="2026-03-31",
                document_type="payroll-salary",
                issuing_party="Example Company, Unipessoal Lda.",
                document_title="Março 2026",
                total_amount=None,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        data = json.loads(statement.with_suffix(".reconciliation.json").read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 2)
        self.assertEqual(data["summary"]["incomplete"], 0)
        files_by_row = {match["row"]: set(match["files"]) for match in data["matches"]}
        self.assertEqual(files_by_row[9], {employee-one_bank_note.name, salary_slip.name})
        self.assertEqual(files_by_row[10], {employee-two_bank_note.name, salary_slip.name})

    def test_evidence_matches_bpi_fee_invoice_line_item(self):
        statement = self._bpi_statement(
            [
                {
                    "date_posting": "08-04-2026",
                    "date_value": "08-04-2026",
                    "description": "MANUTENCAO DE CONTA VALOR NEGOCIOS MAR 2026",
                    "amount": -7.99,
                    "currency": "EUR",
                }
            ]
        )
        invoice = self.export / "CMP_bpi-fees.pdf"
        create_pdf(
            invoice,
            [
                "\n".join(
                    [
                        "FACTURA",
                        "Data de Emissão: 30-04-2026",
                        "MANUTENÇÃO DE CONTA VALOR NEGÓCIOS MAR 2026",
                        "8,31",
                        "16,81",
                        "11,06",
                        "7,99",
                    ]
                )
            ],
        )
        self.repository.save_document(
            invoice,
            self._metadata(
                "bpifee",
                "bpifee",
                date_issued="2026-04-30",
                document_type="invoice",
                issuing_party="BPI",
                document_title="Comissões de conta e títulos",
                total_amount=63.08,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        row = self._only_match(statement)
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["files"], [invoice.name])

    def test_evidence_matches_bpi_maintenance_invoice_to_fee_and_stamp_duty(self):
        statement = self._bpi_statement(
            [
                {
                    "date_posting": "06-02-2026",
                    "date_value": "06-02-2026",
                    "description": "IMPOSTO DE SELO JAN 2026",
                    "amount": -0.32,
                    "currency": "EUR",
                },
                {
                    "date_posting": "06-02-2026",
                    "date_value": "06-02-2026",
                    "description": "MANUTENCAO DE CONTA VALOR NEGOCIOS JAN 2026",
                    "amount": -7.99,
                    "currency": "EUR",
                },
            ]
        )
        invoice = self.export / "CMP_bpi-maintenance.pdf"
        create_pdf(
            invoice,
            [
                "\n".join(
                    [
                        "FACTURA",
                        "Data de Emissão: 28-02-2026",
                        "MANUTENÇÃO DE CONTA VALOR NEGÓCIOS JAN 2026",
                        "8,31",
                        "16,81",
                        "11,06",
                        "7,99",
                    ]
                )
            ],
        )
        self.repository.save_document(
            invoice,
            self._metadata(
                "bpimaint",
                "bpimaint",
                date_issued="2026-02-28",
                document_type="invoice",
                issuing_party="BPI",
                document_title="Manutencao de Conta Valor Negocios",
                total_amount=8.31,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        data = json.loads(statement.with_suffix(".reconciliation.json").read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 2)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["summary"]["unmatched"], 0)
        files_by_row = {match["row"]: match["files"] for match in data["matches"]}
        self.assertEqual(files_by_row[19], [invoice.name])
        self.assertEqual(files_by_row[20], [invoice.name])

    def test_evidence_matches_millennium_fee_invoice_receipt_line_items(self):
        statement = self._millennium_statement(
            [
                {
                    "date_posting": "13/04/2026",
                    "date_value": "13/04/2026",
                    "description": "IMPOSTO DO SELO",
                    "amount": -0.03,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
                {
                    "date_posting": "13/04/2026",
                    "date_value": "13/04/2026",
                    "description": "CUSTO DE SERVICO INTERNACIONAL",
                    "amount": -0.69,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
                {
                    "date_posting": "04/04/2026",
                    "date_value": "04/04/2026",
                    "description": "IMPOSTO SELO ART 17.3.4",
                    "amount": -0.60,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
                {
                    "date_posting": "04/04/2026",
                    "date_value": "04/04/2026",
                    "description": "COM.MAN.CONTA PACOTE M EMPRESA           032026",
                    "amount": -15.00,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
            ]
        )
        stamp_note = self.export / "BNC_international-stamp.pdf"
        service_note = self.export / "BNC_international-service.pdf"
        international_invoice = self.export / "CMP_international-service.pdf"
        maintenance_invoice = self.export / "CMP_package-maintenance.pdf"
        create_pdf(stamp_note, ["Imposto do selo"])
        create_pdf(service_note, ["Custo de servico internacional"])
        create_pdf(
            international_invoice,
            [
                "\n".join(
                    [
                        "Fatura-Recibo",
                        "Data emissão: 2026-04-30",
                        "Custo de Servico Internacional (i)",
                        "0,69",
                        "EUR",
                        "Imposto do Selo - Artº 17.3.4 da Tabela Geral - 4%",
                        "0,03",
                        "EUR",
                        "Total de faturação",
                        "0,72",
                    ]
                )
            ],
        )
        create_pdf(
            maintenance_invoice,
            [
                "\n".join(
                    [
                        "Fatura-Recibo",
                        "Data emissão: 2026-04-04",
                        "Data do Movimento",
                        "2026-04-04",
                        "Comissão referente a  03/2026          (1)",
                        "15,00",
                        "EUR",
                        "Imp. Selo art.17.3.4 da Tab. Geral - 4%",
                        "0,60",
                        "EUR",
                        "Total de Faturação",
                        "15,60",
                    ]
                )
            ],
        )
        self.repository.save_document(
            stamp_note,
            self._metadata("stamp1", "stamp1", date_issued="2026-04-13", document_type="bank-note", issuing_party="MillenniumBCP", total_amount=0.03),
        )
        self.repository.save_document(
            service_note,
            self._metadata("service1", "service1", date_issued="2026-04-13", document_type="bank-note", issuing_party="MillenniumBCP", total_amount=0.69),
        )
        self.repository.save_document(
            international_invoice,
            self._metadata(
                "intl1",
                "intl1",
                date_issued="2026-04-30",
                document_type="invoice-receipt",
                issuing_party="MillenniumBCP",
                document_title="Custo de Serviço Internacional",
                total_amount=0.72,
            ),
        )
        self.repository.save_document(
            maintenance_invoice,
            self._metadata(
                "maint1",
                "maint1",
                date_issued="2026-04-04",
                document_type="invoice-receipt",
                issuing_party="MillenniumBCP",
                document_title="MAN. CTA PACOTE M EMPRESA",
                total_amount=15.60,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        data = json.loads(statement.with_suffix(".reconciliation.json").read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 4)
        self.assertEqual(data["summary"]["incomplete"], 0)
        files_by_row = {match["row"]: set(match["files"]) for match in data["matches"]}
        self.assertEqual(files_by_row[9], {stamp_note.name, international_invoice.name})
        self.assertEqual(files_by_row[10], {service_note.name, international_invoice.name})
        self.assertEqual(files_by_row[11], {maintenance_invoice.name})
        self.assertEqual(files_by_row[12], {maintenance_invoice.name})

    def test_evidence_matches_bpi_stock_sale_line_item(self):
        statement = self._bpi_statement(
            [
                {
                    "date_posting": "01-04-2026",
                    "date_value": "01-04-2026",
                    "description": "TRANSFERENCIA A CREDITO LISTESTREFA",
                    "amount": 10565.32,
                    "currency": "EUR",
                }
            ]
        )
        invoice = self.export / "CMP_bpi-stock.pdf"
        create_pdf(
            invoice,
            [
                "\n".join(
                    [
                        "FACTURA",
                        "Data de Emissão: 30-04-2026",
                        "TÍTULOS",
                        "01/04 01/04",
                        "TOTAL A CRÉDITO",
                        "12 280,81 USD",
                        "VENDA DE 100,0000 ACÇÕES STRATEGY INC(XNGS)",
                        "Nº ORDEM: V7538895",
                    ]
                )
            ],
        )
        self.repository.save_document(
            invoice,
            self._metadata(
                "bpistock",
                "bpistock",
                date_issued="2026-04-30",
                document_type="invoice",
                issuing_party="BPI",
                document_title="Comissões de conta e títulos",
                total_amount=63.08,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        row = self._only_match(statement)
        self.assertEqual(row["errors"], [])
        self.assertEqual(row["files"], [invoice.name])

    def _millennium_statement(self, transactions: list[dict]) -> Path:
        statement = self.export / "statement.xlsx"
        create_millennium_statement(
            statement,
            period_start="01/04/2026",
            period_end="30/04/2026",
            transactions=transactions,
        )
        self._save_statement(statement)
        return statement

    def _bpi_statement(self, transactions: list[dict]) -> Path:
        statement = self.export / "statement.xlsx"
        create_bpi_statement(statement, transactions=transactions)
        self._save_statement(statement)
        return statement

    def _save_statement(self, statement: Path) -> None:
        statement_hash = hash_file_fast(statement)
        metadata = classify_bank_statement(statement, statement_hash)
        self.repository.save_document(statement, metadata)

    def _only_match(self, statement: Path) -> dict:
        data = json.loads(statement.with_suffix(".reconciliation.json").read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["summary"]["unmatched"], 0)
        return data["matches"][0]

    def _metadata(
        self,
        hash_content: str,
        hash_file: str,
        *,
        date_issued: str = "2026-04-15",
        document_type: str,
        issuing_party: str,
        document_title: str | None = None,
        issuer_tax_number: str | None = None,
        total_amount: float = 12.34,
        **overrides,
    ) -> DocumentMetadata:
        data = {
            "class_confidence": 1.0,
            "class_reasoning": "test",
            "date_created": date_issued,
            "date_issued": date_issued,
            "date_updated": date_issued,
            "document_type": document_type,
            "document_type_raw": document_type,
            "issuing_party": issuing_party,
            "issuing_party_raw": issuing_party,
            "document_title": document_title,
            "issuer_tax_number": issuer_tax_number,
            "total_amount": total_amount,
            "total_amount_currency": "EUR",
            "hash_content": hash_content,
            "hash_file": hash_file,
        }
        data.update(overrides)
        return DocumentMetadata(**data)


if __name__ == "__main__":
    unittest.main()
