import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from papertrail.commands import copy_matching, reconcile, rename, review
from papertrail.commands.reconcile import discover_statements_requiring_reconciliation
from papertrail.hashing import hash_file_fast
from papertrail.models import DocumentMetadata
from papertrail.repository import DocumentRepository

from tests.support import create_millennium_statement, create_pdf, make_test_runtime


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.runtime = make_test_runtime(self.root)
        self.repository = DocumentRepository(self.runtime)
        self.processed = self.runtime.paths.processed
        self.export = self.runtime.paths.export

    def tearDown(self):
        self.tmpdir.cleanup()

    def _metadata(self, hash_content: str, hash_file: str, **overrides) -> DocumentMetadata:
        data = {
            "class_confidence": 1.0,
            "class_reasoning": "test",
            "date_created": "2026-01-02",
            "date_issued": "2026-01-02",
            "date_updated": "2026-01-02",
            "document_type": "invoice",
            "issuing_party": "vendor",
            "total_amount": 12.34,
            "total_amount_currency": "EUR",
            "hash_content": hash_content,
            "hash_file": hash_file,
            "document_type_raw": "Invoice",
            "document_title": "Subscription",
            "issuing_party_raw": "Vendor, Inc.",
        }
        data.update(overrides)
        return DocumentMetadata(**data)

    def test_copy_matching_applies_prefix_rules_and_content_dedup(self):
        self.runtime.profile.export.file_mappings.enabled = True
        self.runtime.profile.export.file_mappings.default_prefix = "DIV_"
        self.runtime.profile.export.file_mappings.rules = [
            {"match": {"document_type": "invoice", "issuer_tax_number": "${profile.tax_number}"}, "prefix": "VND_"},
            {"match": {"document_type": "invoice"}, "prefix": "CMP_"},
        ]
        self.runtime.profile.export.file_mappings.filename_fields = ["date_issued", "document_type", "issuing_party"]
        self.runtime.profile.profile.tax_number = "TESTOWNER"

        first_pdf = self.processed / "2026-01-02 - first.pdf"
        second_pdf = self.processed / "2026-01-02 - second.pdf"
        create_pdf(first_pdf, ["invoice one"])
        create_pdf(second_pdf, ["invoice two"])
        self.repository.save_document(
            first_pdf,
            self._metadata("shared123", "file1111", issuer_tax_number="TESTOWNER"),
        )
        self.repository.save_document(
            second_pdf,
            self._metadata("shared123", "file2222", issuer_tax_number="TESTUNKNOWN"),
        )

        dest = self.root / "copied"
        stats = copy_matching(
            self.runtime,
            self.processed,
            "2026-01",
            dest,
            export_config=self.runtime.profile.export,
            profile_context={"tax_number": "TESTOWNER"},
            quiet=True,
        )

        copied_docs = sorted(path.name for path in dest.glob("*.pdf"))
        copied_sidecars = sorted(path.name for path in dest.glob("*.json"))
        self.assertEqual(stats["copied"], 1)
        self.assertEqual(stats["deduped"], 1)
        self.assertEqual(len(copied_docs), 1)
        self.assertEqual(len(copied_sidecars), 1)
        self.assertTrue(copied_docs[0].startswith(("VND_", "CMP_")))

    def test_copy_matching_compresses_exported_pdfs(self):
        source_pdf = self.processed / "2026-01-02 - invoice.pdf"
        create_pdf(source_pdf, ["invoice"])
        self.repository.save_document(source_pdf, self._metadata("hash1111", "file1111"))

        dest = self.root / "copied"
        with patch("papertrail.commands._compress_pdf_export") as compress_mock:
            stats = copy_matching(
                self.runtime,
                self.processed,
                "2026-01",
                dest,
                quiet=True,
            )

        self.assertEqual(stats["copied"], 1)
        exported_pdf = next(dest.glob("*.pdf"))
        compress_mock.assert_called_once_with(exported_pdf)

    def test_reconcile_writes_reconciliation_sidecar(self):
        statement_path = self.export / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            transactions=[
                {
                    "date_posting": "15/01/2026",
                    "date_value": "15/01/2026",
                    "description": "STORE PAYMENT",
                    "amount": -12.34,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        bank_note_path = self.export / "bank-note.pdf"
        receipt_path = self.export / "receipt.pdf"
        create_pdf(bank_note_path, ["Bank note"])
        create_pdf(receipt_path, ["Receipt"])
        self.repository.save_document(
            bank_note_path,
            self._metadata("bank0001", "bank0001", document_type="bank-note", document_type_raw="Bank Note"),
        )
        self.repository.save_document(
            receipt_path,
            self._metadata("recv0001", "recv0001", document_type="receipt", document_type_raw="Receipt"),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        self.assertTrue(sidecar_path.exists())
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["source"], statement_path.name)
        self.assertEqual(data["summary"]["total"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(len(data["matches"]), 1)
        self.assertIn("files", data["matches"][0])
        self.assertIn("unmatched_files", data)

    def test_reconcile_merges_tax_attachments_and_is_idempotent(self):
        self.runtime.profile.reconciliation.rules = [
            {
                "name": "tax-social-security",
                "match_description": ["PTU-TAXA SOCIAL UNICA"],
                "required_types": {"bank-note": 1, "payroll-social": 1},
                "shared_types": {},
                "companions": [],
                "expected_page_count": {},
            },
            {
                "name": "tax-irs",
                "match_description": ["PAG.DUC"],
                "required_types": {"bank-note": 1, "tax-irs": 1},
                "shared_types": {},
                "companions": [],
                "expected_page_count": {},
            },
        ]
        self.runtime.profile.export.merge_rules = [
            {"target_type": "payroll-social", "attach_type": "bank-note"},
            {"target_type": "tax-irs", "attach_type": "bank-note"},
        ]

        statement_path = self.export / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/03/2026",
            period_end="31/03/2026",
            transactions=[
                {
                    "date_posting": "17/03/2026",
                    "date_value": "17/03/2026",
                    "description": "PTU-TAXA SOCIAL UNICA TESTCOMPANY 202602",
                    "amount": -596.83,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
                {
                    "date_posting": "17/03/2026",
                    "date_value": "17/03/2026",
                    "description": "PAG.DUC -TAXREF001",
                    "amount": -6.00,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        social_target = self.export / "payroll-social.pdf"
        social_attachment = self.export / "payroll-social-bank-note.pdf"
        irs_target = self.export / "tax-irs.pdf"
        irs_attachment = self.export / "tax-irs-bank-note.pdf"
        create_pdf(social_target, ["Payroll social"])
        create_pdf(social_attachment, ["Payroll social bank note"])
        create_pdf(irs_target, ["Tax IRS"])
        create_pdf(irs_attachment, ["Tax IRS bank note"])

        self.repository.save_document(
            social_target,
            self._metadata(
                "social001",
                "social001",
                date_created="2026-03-17",
                date_issued="2026-03-17",
                date_updated="2026-03-17",
                document_type="payroll-social",
                document_type_raw="Payroll Social",
                issuing_party="Seguranca Social",
                issuing_party_raw="Seguranca Social",
                document_title="Ficheiro de remuneracoes",
                total_amount=596.83,
            ),
        )
        self.repository.save_document(
            social_attachment,
            self._metadata(
                "socialbn",
                "socialbn",
                date_created="2026-03-17",
                date_issued="2026-03-17",
                date_updated="2026-03-17",
                document_type="bank-note",
                document_type_raw="Movimento",
                issuing_party="MillenniumBCP",
                issuing_party_raw="MillenniumBCP",
                document_title="Transferencia pontual a debito",
                total_amount=596.83,
            ),
        )
        self.repository.save_document(
            irs_target,
            self._metadata(
                "irsdoc01",
                "irsdoc01",
                date_created="2026-03-17",
                date_issued="2026-03-17",
                date_updated="2026-03-17",
                document_type="tax-irs",
                document_type_raw="Tax IRS",
                issuing_party="AT",
                issuing_party_raw="AT",
                document_title="Periodo 20262",
                total_amount=6.00,
            ),
        )
        self.repository.save_document(
            irs_attachment,
            self._metadata(
                "irsbn001",
                "irsbn001",
                date_created="2026-03-17",
                date_issued="2026-03-17",
                date_updated="2026-03-17",
                document_type="bank-note",
                document_type_raw="Movimento",
                issuing_party="MillenniumBCP",
                issuing_party_raw="MillenniumBCP",
                document_title="Pagamento de servicos",
                total_amount=6.00,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        with fitz.open(social_target) as doc:
            self.assertEqual(len(doc), 2)
        with fitz.open(irs_target) as doc:
            self.assertEqual(len(doc), 2)

        reconcile(self.runtime, self.export, dry_run=False)

        with fitz.open(social_target) as doc:
            self.assertEqual(len(doc), 2)
        with fitz.open(irs_target) as doc:
            self.assertEqual(len(doc), 2)

    def test_discover_statements_requiring_reconciliation_flags_missing_and_stale(self):
        statement_path = self.export / "statement.xlsx"
        create_millennium_statement(statement_path)

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        receipt_path = self.export / "receipt.pdf"
        create_pdf(receipt_path, ["Receipt"])
        self.repository.save_document(
            receipt_path,
            self._metadata("recv0001", "recv0001", document_type="receipt", document_type_raw="Receipt"),
        )

        pending = discover_statements_requiring_reconciliation(self.repository, self.export)
        self.assertEqual(pending, [statement_path])

        reconciliation_path = statement_path.with_suffix(".reconciliation.json")
        reconciliation_path.write_text('{"source":"statement.xlsx"}\n', encoding="utf-8")

        latest_input_mtime = max(
            statement_path.stat().st_mtime,
            statement_path.with_suffix(".json").stat().st_mtime,
            receipt_path.stat().st_mtime,
            receipt_path.with_suffix(".json").stat().st_mtime,
        )
        os.utime(reconciliation_path, (latest_input_mtime + 5, latest_input_mtime + 5))

        pending = discover_statements_requiring_reconciliation(self.repository, self.export)
        self.assertEqual(pending, [])

        os.utime(receipt_path, (latest_input_mtime + 10, latest_input_mtime + 10))
        pending = discover_statements_requiring_reconciliation(self.repository, self.export)
        self.assertEqual(pending, [statement_path])

    def test_discover_statements_requiring_reconciliation_can_ignore_stale_sidecars(self):
        statement_path = self.export / "statement.xlsx"
        create_millennium_statement(statement_path)

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        receipt_path = self.export / "receipt.pdf"
        create_pdf(receipt_path, ["Receipt"])
        self.repository.save_document(
            receipt_path,
            self._metadata("recv0001", "recv0001", document_type="receipt", document_type_raw="Receipt"),
        )

        reconciliation_path = statement_path.with_suffix(".reconciliation.json")
        reconciliation_path.write_text('{"source":"statement.xlsx"}\n', encoding="utf-8")

        latest_input_mtime = max(
            statement_path.stat().st_mtime,
            statement_path.with_suffix(".json").stat().st_mtime,
            receipt_path.stat().st_mtime,
            receipt_path.with_suffix(".json").stat().st_mtime,
        )
        os.utime(reconciliation_path, (latest_input_mtime - 5, latest_input_mtime - 5))

        pending = discover_statements_requiring_reconciliation(
            self.repository,
            self.export,
            include_stale=False,
        )
        self.assertEqual(pending, [])

    def test_rename_rejects_export_directory(self):
        with self.assertRaisesRegex(RuntimeError, "rename cannot be run on export directories"):
            rename(self.runtime, self.export, quiet=True)

    def test_review_does_not_rerun_reconciliation(self):
        statement_path = self.export / "statement.xlsx"
        create_millennium_statement(statement_path)

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        receipt_path = self.export / "receipt.pdf"
        create_pdf(receipt_path, ["Receipt"])
        self.repository.save_document(
            receipt_path,
            self._metadata("recv0001", "recv0001", document_type="receipt", document_type_raw="Receipt"),
        )

        reconciliation_path = statement_path.with_suffix(".reconciliation.json")
        reconciliation_path.write_text('{"source":"statement.xlsx"}\n', encoding="utf-8")

        latest_input_mtime = max(
            statement_path.stat().st_mtime,
            statement_path.with_suffix(".json").stat().st_mtime,
            receipt_path.stat().st_mtime,
            receipt_path.with_suffix(".json").stat().st_mtime,
        )
        os.utime(receipt_path, (latest_input_mtime + 10, latest_input_mtime + 10))

        with (
            patch("papertrail.commands.reconcile_single") as reconcile_single_mock,
            patch("tools.shared.launch_tool") as launch_tool_mock,
        ):
            review(self.runtime, self.export)

        reconcile_single_mock.assert_not_called()
        launch_tool_mock.assert_called_once_with("review")

    def test_reconcile_keeps_pdf_bank_screenshots_and_prefers_nearest_same_signature(self):
        feb_dir = self.export / "2026-02"
        mar_dir = self.export / "2026-03"
        feb_dir.mkdir()
        mar_dir.mkdir()

        statement_path = feb_dir / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/02/2026",
            period_end="28/02/2026",
            transactions=[
                {
                    "date_posting": "13/02/2026",
                    "date_value": "13/02/2026",
                    "description": "MDB1717 MDB Google Y 17.99EUR",
                    "amount": -17.99,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        bank_screenshot = feb_dir / "google-bank.pdf"
        unrelated_statement = feb_dir / "account-summary.pdf"
        feb_invoice = feb_dir / "google-feb.pdf"
        mar_invoice = mar_dir / "google-mar.pdf"
        create_pdf(bank_screenshot, ["Google movement"])
        create_pdf(unrelated_statement, ["Account summary"])
        create_pdf(feb_invoice, ["Google invoice feb"])
        create_pdf(mar_invoice, ["Google invoice mar"])

        self.repository.save_document(
            bank_screenshot,
            self._metadata(
                "bank1719",
                "bank1719",
                date_created="2026-02-13",
                date_issued="2026-02-13",
                date_updated="2026-02-13",
                document_type="bank-statement",
                document_type_raw="Movimento",
                issuing_party="Google",
                issuing_party_raw="Google Y",
                document_title="Google Y",
                total_amount=17.99,
            ),
        )
        self.repository.save_document(
            unrelated_statement,
            self._metadata(
                "acct1719",
                "acct1719",
                date_created="2026-02-06",
                date_issued="2026-02-06",
                date_updated="2026-02-06",
                document_type="bank-statement",
                document_type_raw="Extrato",
                issuing_party="MillenniumBCP",
                issuing_party_raw="MillenniumBCP",
                document_title="Conta a ordem",
                total_amount=17.99,
            ),
        )
        self.repository.save_document(
            feb_invoice,
            self._metadata(
                "invfeb17",
                "invfeb17",
                date_created="2026-02-10",
                date_issued="2026-02-10",
                date_updated="2026-02-10",
                document_type="invoice",
                document_type_raw="Invoice",
                issuing_party="Google",
                issuing_party_raw="Google",
                document_title="YouTube Premium",
                total_amount=17.99,
            ),
        )
        self.repository.save_document(
            mar_invoice,
            self._metadata(
                "invmar17",
                "invmar17",
                date_created="2026-03-10",
                date_issued="2026-03-10",
                date_updated="2026-03-10",
                document_type="invoice",
                document_type_raw="Invoice",
                issuing_party="Google",
                issuing_party_raw="Google",
                document_title="YouTube Premium",
                total_amount=17.99,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(
            sorted(data["matches"][0]["files"]),
            sorted(["google-bank.pdf", "google-feb.pdf"]),
        )

    def test_reconcile_deduplicates_same_document_copied_into_multiple_export_months(self):
        jan_dir = self.export / "2026-01"
        feb_dir = self.export / "2026-02"
        jan_dir.mkdir()
        feb_dir.mkdir()

        statement_path = feb_dir / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/02/2026",
            period_end="28/02/2026",
            transactions=[
                {
                    "date_posting": "15/02/2026",
                    "date_value": "15/02/2026",
                    "description": "STORE PAYMENT",
                    "amount": -12.34,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        bank_note_path = feb_dir / "bank-note.pdf"
        receipt_jan = jan_dir / "receipt-copy-a.pdf"
        receipt_feb = feb_dir / "receipt-copy-b.pdf"
        create_pdf(bank_note_path, ["Bank note"])
        create_pdf(receipt_jan, ["Receipt"])
        create_pdf(receipt_feb, ["Receipt"])

        self.repository.save_document(
            bank_note_path,
            self._metadata(
                "bank1234",
                "bank1234",
                date_created="2026-02-15",
                date_issued="2026-02-15",
                date_updated="2026-02-15",
                document_type="bank-note",
                document_type_raw="Bank Note",
                total_amount=12.34,
            ),
        )

        duplicate_receipt = self._metadata(
            "recv1234",
            "sharedrcp",
            date_created="2026-02-14",
            date_issued="2026-02-14",
            date_updated="2026-02-14",
            document_type="receipt",
            document_type_raw="Receipt",
            total_amount=12.34,
        )
        self.repository.save_document(receipt_jan, duplicate_receipt)
        self.repository.save_document(receipt_feb, duplicate_receipt)

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(len(data["matches"][0]["files"]), 2)

    def test_reconcile_does_not_reuse_exact_match_for_duplicate_amount_transactions(self):
        self.runtime.profile.reconciliation.rules = [
            {
                "name": "bank-transfer-internal",
                "match_description": ["TRF P/ Example Company - BPI"],
                "required_types": {"bank-note": 1},
                "shared_types": {},
                "companions": [],
                "expected_page_count": {},
            },
        ]

        statement_path = self.export / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/04/2026",
            period_end="30/04/2026",
            transactions=[
                {
                    "date_posting": "23/04/2026",
                    "date_value": "23/04/2026",
                    "description": "TRF P/ Example Company - BPI",
                    "amount": -5000.00,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
                {
                    "date_posting": "23/04/2026",
                    "date_value": "23/04/2026",
                    "description": "TRF P/ Example Company - BPI",
                    "amount": -5000.00,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        bank_note_path = self.export / "bank-note.pdf"
        create_pdf(bank_note_path, ["Bank note"])
        self.repository.save_document(
            bank_note_path,
            self._metadata(
                "bank5000",
                "bank5000",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="MillenniumBCP",
                issuing_party_raw="MillenniumBCP",
                document_title="Transferencia pontual a debito SEPA+",
                total_amount=5000.00,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["summary"]["unmatched"], 1)
        self.assertEqual(len(data["matches"]), 1)
        self.assertEqual(data["matches"][0]["files"], ["bank-note.pdf"])
        self.assertEqual(
            sum(match["files"].count("bank-note.pdf") for match in data["matches"]),
            1,
        )

    def test_reconcile_links_same_day_no_amount_contract_document(self):
        self.runtime.profile.reconciliation.rules = [
            {
                "name": "loan-millennium-disbursement",
                "match_description": ["CONCESS CRED EMPR"],
                "required_types": {"bank-note|bank-transfer|contract-signup": [1, None]},
                "shared_types": {},
                "companions": [],
                "expected_page_count": {"bank-note|bank-transfer": 1},
            },
        ]

        statement_path = self.export / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/04/2026",
            period_end="30/04/2026",
            transactions=[
                {
                    "date_posting": "23/04/2026",
                    "date_value": "23/04/2026",
                    "description": "CONCESS CRED EMPR MN  NR.       LOANREF001",
                    "amount": 25000.00,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        bank_transfer_path = self.export / "bank-transfer.pdf"
        contract_path = self.export / "contract-signup.pdf"
        signature_report_path = self.export / "signature-report.pdf"
        create_pdf(bank_transfer_path, ["Concess credito empr MN nr. LOANREF001"])
        create_pdf(contract_path, ["Contrato de Credito Digital Finance Desk"])
        create_pdf(signature_report_path, ["Relatorio final de recolha de assinaturas"])

        self.repository.save_document(
            bank_transfer_path,
            self._metadata(
                "bank2500",
                "bank2500",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="bank-transfer",
                document_type_raw="Transferencia",
                issuing_party="BPI",
                issuing_party_raw="BPI",
                document_title="Concess credito empr MN nr. LOANREF001",
                total_amount=25000.00,
            ),
        )
        self.repository.save_document(
            contract_path,
            self._metadata(
                "contract",
                "contract",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="contract-signup",
                document_type_raw="Contrato de Credito Digital Finance Desk",
                issuing_party="millenniumbcp",
                issuing_party_raw="Banco Comercial Portugues, S.A.",
                document_title="Credito Digital Finance Desk",
                total_amount=25000.00,
            ),
        )
        self.repository.save_document(
            signature_report_path,
            self._metadata(
                "sigrep01",
                "sigrep01",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="contract-signup",
                document_type_raw="Contrato de Credito Digital Finance Desk",
                issuing_party="millenniumbcp",
                issuing_party_raw="Banco Comercial Portugues, S.A.",
                document_title="Relatorio final de recolha de assinaturas",
                total_amount=None,
                total_amount_currency=None,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(
            set(data["matches"][0]["files"]),
            {"bank-transfer.pdf", "contract-signup.pdf", "signature-report.pdf"},
        )
        self.assertEqual(data["unmatched_files"], [])

    def test_reconcile_shared_matching_prefers_nearest_shared_document_per_signature(self):
        self.runtime.profile.reconciliation.rules = [
            {
                "name": "vendor-sharedtoll",
                "match_description": ["SHAREDTOLL"],
                "required_types": {"bank-note": 1, "receipt|invoice-receipt": [1, None]},
                "shared_types": {"invoice-receipt": "Shared Toll"},
                "companions": [],
                "expected_page_count": {},
            },
        ]

        jan_dir = self.export / "2026-01"
        feb_dir = self.export / "2026-02"
        jan_dir.mkdir()
        feb_dir.mkdir()

        statement_path = feb_dir / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/02/2026",
            period_end="28/02/2026",
            transactions=[
                {
                    "date_posting": "23/02/2026",
                    "date_value": "23/02/2026",
                    "description": "MDB TESTREF PAG BX VAL-SHAREDTOLL MOV 15",
                    "amount": -5.70,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        bank_note_path = feb_dir / "shared-toll-bank-note.pdf"
        shared_jan_path = jan_dir / "shared-toll-jan.pdf"
        shared_feb_path = feb_dir / "shared-toll-feb.pdf"
        create_pdf(bank_note_path, ["Shared Toll movement"])
        create_pdf(shared_jan_path, ["Shared Toll january receipt"])
        create_pdf(shared_feb_path, ["Shared Toll february receipt"])

        self.repository.save_document(
            bank_note_path,
            self._metadata(
                "bank5700",
                "bank5700",
                date_created="2026-02-23",
                date_issued="2026-02-23",
                date_updated="2026-02-23",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="Shared Toll",
                issuing_party_raw="Shared Toll",
                document_title="Shared Toll movement",
                total_amount=5.70,
            ),
        )
        self.repository.save_document(
            shared_jan_path,
            self._metadata(
                "sharedjan",
                "sharedjan",
                date_created="2026-01-31",
                date_issued="2026-01-31",
                date_updated="2026-01-31",
                document_type="receipt",
                document_type_raw="Extrato/Recibo",
                issuing_party="Shared Toll",
                issuing_party_raw="Shared Toll",
                document_title="Pagamentos de Servicos Shared Toll",
                total_amount=26.51,
            ),
        )
        self.repository.save_document(
            shared_feb_path,
            self._metadata(
                "sharedfeb",
                "sharedfeb",
                date_created="2026-02-28",
                date_issued="2026-02-28",
                date_updated="2026-02-28",
                document_type="invoice-receipt",
                document_type_raw="Extrato/Recibo",
                issuing_party="Shared Toll",
                issuing_party_raw="Shared Toll",
                document_title="Pagamentos de Servicos Shared Toll",
                total_amount=18.25,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(
            sorted(data["matches"][0]["files"]),
            sorted(["shared-toll-bank-note.pdf", "shared-toll-feb.pdf"]),
        )

    def test_reconcile_does_not_use_adjacent_month_shared_doc_for_via_verde(self):
        self.runtime.profile.reconciliation.rules = [
            {
                "name": "vendor-sharedtoll",
                "match_description": ["SHAREDTOLL"],
                "required_types": {"bank-note": 1, "receipt|invoice-receipt": [1, None]},
                "shared_types": {"invoice-receipt": "Shared Toll"},
                "companions": [],
                "expected_page_count": {},
            },
        ]

        feb_dir = self.export / "2026-02"
        mar_dir = self.export / "2026-03"
        feb_dir.mkdir()
        mar_dir.mkdir()

        statement_path = mar_dir / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/03/2026",
            period_end="31/03/2026",
            transactions=[
                {
                    "date_posting": "02/03/2026",
                    "date_value": "02/03/2026",
                    "description": "MDB TESTREF PAG BX VAL-SHAREDTOLL MOV 16",
                    "amount": -4.00,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        bank_note_path = mar_dir / "shared-toll-bank-note.pdf"
        shared_feb_path = feb_dir / "shared-toll-feb.pdf"
        create_pdf(bank_note_path, ["Shared Toll movement"])
        create_pdf(shared_feb_path, ["Shared Toll february receipt"])

        self.repository.save_document(
            bank_note_path,
            self._metadata(
                "bank4000",
                "bank4000",
                date_created="2026-03-02",
                date_issued="2026-03-02",
                date_updated="2026-03-02",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="Shared Toll",
                issuing_party_raw="Shared Toll",
                document_title="Shared Toll movement",
                total_amount=4.00,
            ),
        )
        self.repository.save_document(
            shared_feb_path,
            self._metadata(
                "sharedfeb",
                "sharedfeb",
                date_created="2026-02-28",
                date_issued="2026-02-28",
                date_updated="2026-02-28",
                document_type="invoice-receipt",
                document_type_raw="Extrato/Recibo",
                issuing_party="Shared Toll",
                issuing_party_raw="Shared Toll",
                document_title="Pagamentos de Servicos Shared Toll",
                total_amount=18.25,
            ),
        )

        reconcile(self.runtime, mar_dir, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 0)
        self.assertEqual(data["summary"]["incomplete"], 1)
        self.assertEqual(data["matches"][0]["files"], ["shared-toll-bank-note.pdf"])
        self.assertIn("missing receipt/invoice-receipt", data["matches"][0]["errors"][0])

    def test_reconcile_prunes_cross_month_bank_generated_candidates_when_same_month_exists(self):
        self.runtime.profile.reconciliation.rules = [
            {
                "name": "bank-fee-stamp-duty",
                "match_description": ["IMPOSTO DO SELO"],
                "required_types": {"invoice-receipt": 1, "bank-note": [0, 1]},
                "shared_types": {},
                "companions": [],
                "expected_page_count": {},
            },
        ]

        feb_dir = self.export / "2026-02"
        mar_dir = self.export / "2026-03"
        feb_dir.mkdir()
        mar_dir.mkdir()

        statement_path = mar_dir / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/03/2026",
            period_end="31/03/2026",
            transactions=[
                {
                    "date_posting": "13/03/2026",
                    "date_value": "13/03/2026",
                    "description": "IMPOSTO DO SELO",
                    "amount": -0.03,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        same_month_bank_note = mar_dir / "march-stamp-duty.pdf"
        prior_month_bank_note = feb_dir / "february-stamp-duty.pdf"
        unrelated_cross_month_bank_transfer = mar_dir / "bank-transfer-fee.pdf"
        invoice_receipt = mar_dir / "march-invoice-receipt.pdf"
        create_pdf(same_month_bank_note, ["March stamp duty"])
        create_pdf(prior_month_bank_note, ["February stamp duty"])
        create_pdf(unrelated_cross_month_bank_transfer, ["Unrelated transfer fee"])
        create_pdf(invoice_receipt, ["Invoice receipt"])

        self.repository.save_document(
            same_month_bank_note,
            self._metadata(
                "stampmar0",
                "stampmar0",
                date_created="2026-03-13",
                date_issued="2026-03-13",
                date_updated="2026-03-13",
                document_type="bank-note",
                document_type_raw="Movimento",
                issuing_party="MillenniumBCP",
                issuing_party_raw="MillenniumBCP",
                document_title="Imposto do selo",
                total_amount=0.03,
            ),
        )
        self.repository.save_document(
            prior_month_bank_note,
            self._metadata(
                "stampfeb0",
                "stampfeb0",
                date_created="2026-02-13",
                date_issued="2026-02-13",
                date_updated="2026-02-13",
                document_type="bank-note",
                document_type_raw="Movimento",
                issuing_party="MillenniumBCP",
                issuing_party_raw="MillenniumBCP",
                document_title="Imposto do selo",
                total_amount=0.03,
            ),
        )
        self.repository.save_document(
            unrelated_cross_month_bank_transfer,
            self._metadata(
                "txfer003",
                "txfer003",
                date_created="2026-03-24",
                date_issued="2026-03-24",
                date_updated="2026-03-24",
                document_type="bank-transfer",
                document_type_raw="Transferencia",
                issuing_party="ActivoBank",
                issuing_party_raw="ActivoBank",
                document_title="Transferencia internacional eurid",
                total_amount=0.03,
            ),
        )
        self.repository.save_document(
            invoice_receipt,
            self._metadata(
                "invrcpt0",
                "invrcpt0",
                date_created="2026-03-31",
                date_issued="2026-03-31",
                date_updated="2026-03-31",
                document_type="invoice-receipt",
                document_type_raw="Fatura-Recibo",
                issuing_party="MillenniumBCP",
                issuing_party_raw="Banco Comercial Portugues, S.A.",
                document_title="Custo de Servico Internacional",
                total_amount=0.03,
            ),
        )

        reconcile(self.runtime, mar_dir, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(
            set(data["matches"][0]["files"]),
            {"march-stamp-duty.pdf", "march-invoice-receipt.pdf"},
        )

    def test_reconcile_month_folder_uses_adjacent_month_candidates_selectively(self):
        self.runtime.profile.reconciliation.rules = [
            {
                "name": "benefit-benefits-provider",
                "match_description": ["ORDEM PAGAMENTO S/ESTRANGEIRO"],
                "required_types": {"bank-note": 1, "receipt|invoice-receipt": 1},
                "shared_types": {},
                "companions": [],
                "expected_page_count": {},
            },
            {
                "name": "payroll-salary-employee-one",
                "match_description": ["EMPLOYEE ONE"],
                "required_types": {"bank-note": 1, "payroll-salary": 1},
                "shared_types": {"payroll-salary": None},
                "companions": [],
                "expected_page_count": {},
            },
            {
                "name": "payroll-salary-employee-two",
                "match_description": ["EMPLOYEE TWO"],
                "required_types": {"bank-note": 1, "payroll-salary": 1},
                "shared_types": {"payroll-salary": None},
                "companions": [],
                "expected_page_count": {},
            },
        ]

        jan_dir = self.export / "2026-01"
        feb_dir = self.export / "2026-02"
        mar_dir = self.export / "2026-03"
        jan_dir.mkdir()
        feb_dir.mkdir()
        mar_dir.mkdir()

        statement_path = feb_dir / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/02/2026",
            period_end="28/02/2026",
            transactions=[
                {
                    "date_posting": "27/02/2026",
                    "date_value": "27/02/2026",
                    "description": "Ordem Pagamento s/Estrangeiro   Ref.FOREIGNPAYREF001",
                    "amount": -364.80,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
                {
                    "date_posting": "27/02/2026",
                    "date_value": "27/02/2026",
                    "description": "TRF P/ Employee One",
                    "amount": -964.85,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
                {
                    "date_posting": "27/02/2026",
                    "date_value": "27/02/2026",
                    "description": "TRF P/ Employee Two",
                    "amount": -557.72,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        benefits-provider_bank_note = feb_dir / "benefits-provider-bank-note.pdf"
        employee-one_bank_note = feb_dir / "employee-one-bank-note.pdf"
        employee-two_bank_note = feb_dir / "employee-two-bank-note.pdf"
        payroll_receipt = jan_dir / "january-payroll.pdf"
        benefits-provider_receipt = mar_dir / "march-benefits-provider.pdf"
        unrelated_mar_invoice = mar_dir / "unrelated-march-invoice.pdf"

        create_pdf(benefits-provider_bank_note, ["Benefits Provider bank note"])
        create_pdf(employee-one_bank_note, ["Employee One salary bank note"])
        create_pdf(employee-two_bank_note, ["Employee Two salary bank note"])
        create_pdf(payroll_receipt, ["January payroll receipt"])
        create_pdf(benefits-provider_receipt, ["March benefits-provider receipt"])
        create_pdf(unrelated_mar_invoice, ["Unrelated invoice"])

        self.repository.save_document(
            benefits-provider_bank_note,
            self._metadata(
                "cfbank01",
                "cfbank01",
                date_created="2026-02-27",
                date_issued="2026-02-27",
                date_updated="2026-02-27",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="MillenniumBCP",
                issuing_party_raw="MillenniumBCP",
                document_title="Ordem de pagamento sobre o estrangeiro",
                total_amount=364.80,
            ),
        )
        self.repository.save_document(
            employee-one_bank_note,
            self._metadata(
                "crbank01",
                "crbank01",
                date_created="2026-02-27",
                date_issued="2026-02-27",
                date_updated="2026-02-27",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="MillenniumBCP",
                issuing_party_raw="MillenniumBCP",
                document_title="Transferencia pontual a debito",
                total_amount=964.85,
            ),
        )
        self.repository.save_document(
            employee-two_bank_note,
            self._metadata(
                "tibank01",
                "tibank01",
                date_created="2026-02-27",
                date_issued="2026-02-27",
                date_updated="2026-02-27",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="MillenniumBCP",
                issuing_party_raw="MillenniumBCP",
                document_title="Transferencia pontual a debito",
                total_amount=557.72,
            ),
        )
        self.repository.save_document(
            payroll_receipt,
            self._metadata(
                "payjan01",
                "payjan01",
                date_created="2026-01-31",
                date_issued="2026-01-31",
                date_updated="2026-01-31",
                document_type="payroll-salary",
                document_type_raw="Recibo Remuneracao",
                issuing_party="Example Company, Unipessoal Lda.",
                issuing_party_raw="Example Company, Unipessoal Lda.",
                document_title="Janeiro 2026",
                total_amount=1522.57,
            ),
        )
        self.repository.save_document(
            benefits-provider_receipt,
            self._metadata(
                "covmar01",
                "covmar01",
                date_created="2026-03-05",
                date_issued="2026-03-05",
                date_updated="2026-03-05",
                document_type="receipt",
                document_type_raw="Receipt",
                issuing_party="benefits-provider",
                issuing_party_raw="benefits-provider",
                document_title="Carregamento subsidio de alimentacao",
                total_amount=364.80,
            ),
        )
        self.repository.save_document(
            unrelated_mar_invoice,
            self._metadata(
                "noise001",
                "noise001",
                date_created="2026-03-08",
                date_issued="2026-03-08",
                date_updated="2026-03-08",
                document_type="invoice",
                document_type_raw="Invoice",
                issuing_party="Other Vendor",
                issuing_party_raw="Other Vendor",
                document_title="Unrelated march invoice",
                total_amount=999.99,
            ),
        )

        reconcile(self.runtime, feb_dir, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))

        self.assertEqual(data["summary"]["reconciled"], 3)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["summary"]["unmatched"], 0)

        files_by_row = {match["row"]: set(match["files"]) for match in data["matches"]}
        self.assertEqual(
            files_by_row[9],
            {"benefits-provider-bank-note.pdf", "march-benefits-provider.pdf"},
        )
        self.assertEqual(
            files_by_row[10],
            {"employee-one-bank-note.pdf", "january-payroll.pdf"},
        )
        self.assertEqual(
            files_by_row[11],
            {"employee-two-bank-note.pdf", "january-payroll.pdf"},
        )
        self.assertNotIn(
            "unrelated-march-invoice.pdf",
            {entry["file"] for entry in data["unmatched_files"]},
        )


if __name__ == "__main__":
    unittest.main()
