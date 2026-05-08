import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from papertrail.commands import _run_export_period, copy_matching, reconcile, rename, review
from papertrail.commands.reconcile import discover_statements_requiring_reconciliation
from papertrail.hashing import hash_file_fast
from papertrail.models import DocumentMetadata
from papertrail.reconciliation_groundtruth import GROUNDTRUTH_SUFFIX
from papertrail.repository import DocumentRepository

from tests.support import create_bpi_statement, create_millennium_statement, create_pdf, make_test_runtime


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

    def test_copy_matching_uses_effective_document_type_for_pdf_exports(self):
        self.runtime.profile.export.file_mappings.enabled = True
        self.runtime.profile.export.file_mappings.default_prefix = "DIV_"
        self.runtime.profile.export.file_mappings.rules = [
            {"match": {"document_type": "bank-*"}, "prefix": "BNC_"},
        ]
        self.runtime.profile.export.file_mappings.filename_fields = [
            "date_issued",
            "document_type",
            "issuing_party",
            "document_title",
        ]

        source_pdf = self.processed / "2026-04-27 - bank-note - bpi.pdf"
        create_pdf(source_pdf, ["Mapa resumo de datas e valores de aquisicao de valores mobiliarios"])
        self.repository.save_document(
            source_pdf,
            self._metadata(
                "hash1111",
                "file1111",
                date_issued="2026-04-27",
                document_type="bank-note",
                document_type_raw="Mapa resumo",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="Datas e valores de aquisicao de valores mobiliarios",
            ),
        )

        dest = self.root / "copied"
        stats = copy_matching(
            self.runtime,
            self.processed,
            "2026-04",
            dest,
            export_config=self.runtime.profile.export,
            quiet=True,
        )

        self.assertEqual(stats["copied"], 1)
        exported_pdf = next(dest.glob("*.pdf"))
        self.assertTrue(exported_pdf.name.startswith("DIV_"))
        self.assertLessEqual(len(exported_pdf.name), 60)
        self.assertIn("investment-acquisition", exported_pdf.name)
        self.assertNotIn("datas e valores", exported_pdf.name)
        exported_metadata = json.loads(exported_pdf.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(exported_metadata["document_type"], "investment-acquisition-summary")

    def test_copy_matching_export_names_omit_title_are_ascii_and_cap_pdfs(self):
        self.runtime.profile.export.file_mappings.enabled = True
        self.runtime.profile.export.file_mappings.default_prefix = "DIV_"
        self.runtime.profile.export.file_mappings.rules = [
            {"match": {"document_type": "bank-*"}, "prefix": "BNC_"},
        ]
        self.runtime.profile.export.file_mappings.filename_fields = [
            "date_issued",
            "document_type",
            "issuing_party",
            "document_title",
        ]

        source_pdf = self.processed / "2026-04-30 - payroll.pdf"
        create_pdf(source_pdf, ["Recibo de remuneracao"])
        self.repository.save_document(
            source_pdf,
            self._metadata(
                "hash3333",
                "file3333",
                date_issued="2026-04-30",
                document_type="payroll-vacation",
                document_type_raw="Recibo",
                issuing_party="segurança social",
                issuing_party_raw="Segurança Social",
                document_title="Remuneracao extraordinaria de ferias",
            ),
        )

        dest = self.root / "copied"
        with patch("papertrail.commands._compress_pdf_export"):
            stats = copy_matching(
                self.runtime,
                self.processed,
                "2026-04",
                dest,
                export_config=self.runtime.profile.export,
                quiet=True,
            )

        self.assertEqual(stats["copied"], 1)
        exported_pdf = next(dest.glob("*.pdf"))
        self.assertLessEqual(len(exported_pdf.name), 60)
        self.assertTrue(exported_pdf.name.isascii())
        self.assertNotIn("remuneracao", exported_pdf.name)
        self.assertTrue(exported_pdf.with_suffix(".json").exists())

    def test_copy_matching_exports_loan_disbursement_movement_as_bnc_bank_note(self):
        self.runtime.profile.export.file_mappings.enabled = True
        self.runtime.profile.export.file_mappings.default_prefix = "DIV_"
        self.runtime.profile.export.file_mappings.rules = [
            {"match": {"document_type": "bank-*"}, "prefix": "BNC_"},
        ]
        self.runtime.profile.export.file_mappings.filename_fields = [
            "date_issued",
            "document_type",
            "issuing_party",
            "document_title",
        ]

        source_pdf = self.processed / "2026-04-23 - bank-transfer - millenniumbcp.pdf"
        create_pdf(source_pdf, ["CONCESS CRED EMPR MN NR. LOANREF001"])
        self.repository.save_document(
            source_pdf,
            self._metadata(
                "hash2222",
                "file2222",
                date_issued="2026-04-23",
                document_type="bank-transfer",
                document_type_raw="TRF",
                issuing_party="millenniumbcp",
                issuing_party_raw="Banco Comercial Portugues, S.A.",
                document_title="CONCESS CRED EMPR MN NR. LOANREF001",
                total_amount=25000.0,
            ),
        )

        dest = self.root / "copied"
        stats = copy_matching(
            self.runtime,
            self.processed,
            "2026-04",
            dest,
            export_config=self.runtime.profile.export,
            quiet=True,
        )

        self.assertEqual(stats["copied"], 1)
        exported_pdf = next(dest.glob("*.pdf"))
        self.assertTrue(exported_pdf.name.startswith("BNC_"))
        self.assertIn("bank-note", exported_pdf.name)
        exported_metadata = json.loads(exported_pdf.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(exported_metadata["document_type"], "bank-note")

    def test_copy_matching_dedupes_bank_statements_by_account_and_period(self):
        self.runtime.profile.export.file_mappings.enabled = True
        self.runtime.profile.export.file_mappings.default_prefix = "DIV_"
        self.runtime.profile.export.file_mappings.rules = [
            {"match": {"document_type": "bank-*"}, "prefix": "BNC_"},
        ]
        self.runtime.profile.export.file_mappings.filename_fields = [
            "date_issued",
            "document_type",
            "issuing_party",
            "document_title",
        ]

        bank_statement = {
            "bank_format": "bpi",
            "account_number": "TEST-ACCOUNT-BETA-2",
            "currency": "EUR",
            "period_start": "2026-04-01",
            "period_end": "2026-04-27",
            "transaction_count": 12,
        }
        first_xlsx = self.processed / "2026-04-01 - bank-statement - bpi - aaaa1111.xlsx"
        second_xlsx = self.processed / "2026-04-01 - bank-statement - bpi - zzzz2222.xlsx"
        first_xlsx.write_bytes(b"first")
        second_xlsx.write_bytes(b"second")

        for path, hash_value in [(first_xlsx, "aaaa1111"), (second_xlsx, "zzzz2222")]:
            self.repository.save_document(
                path,
                self._metadata(
                    hash_value,
                    hash_value,
                    date_issued="2026-04-01",
                    document_type="bank-statement",
                    document_type_raw="bank-statement",
                    issuing_party="bpi",
                    issuing_party_raw="BPI",
                    document_title="TEST-ACCOUNT-BETA-2",
                    total_amount=None,
                    total_amount_currency="EUR",
                    source_extension=".xlsx",
                    bank_statement=bank_statement,
                ),
            )

        dest = self.root / "copied"
        stats = copy_matching(
            self.runtime,
            self.processed,
            "2026-04",
            dest,
            export_config=self.runtime.profile.export,
            quiet=True,
        )

        self.assertEqual(stats["copied"], 1)
        self.assertEqual(stats["deduped"], 1)
        exported_files = sorted(path.name for path in dest.glob("*.xlsx"))
        self.assertEqual(len(exported_files), 1)
        self.assertIn("aaaa1111", exported_files[0])

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

    def test_export_rebuild_preserves_reconciliation_groundtruth_by_statement_hash(self):
        from papertrail.bank_statement import classify_bank_statement
        from papertrail.naming import file_name_from_metadata

        source_statement = self.processed / "source-statement.xlsx"
        create_millennium_statement(
            source_statement,
            period_start="01/04/2026",
            period_end="30/04/2026",
        )
        statement_hash = hash_file_fast(source_statement)
        statement_metadata = classify_bank_statement(source_statement, statement_hash)
        processed_statement = self.processed / file_name_from_metadata(statement_metadata, statement_hash)
        source_statement.rename(processed_statement)
        self.repository.save_document(processed_statement, statement_metadata)

        approved_receipt = self.processed / "2026-03-15 - receipt - vendor - doc12345.pdf"
        create_pdf(approved_receipt, ["approved out-of-month receipt"])
        self.repository.save_document(
            approved_receipt,
            self._metadata(
                "content-doc12345",
                "doc12345",
                date_issued="2026-03-15",
                document_type="receipt",
                issuing_party="vendor",
                document_title="Approved out-of-month receipt",
            ),
        )

        export_month = self.export / "2026-04"
        export_month.mkdir()
        old_statement = export_month / "old-statement-name.xlsx"
        old_statement.write_bytes(processed_statement.read_bytes())
        self.repository.save_json(old_statement.with_suffix(".json"), statement_metadata.model_dump())
        old_groundtruth = old_statement.with_suffix(GROUNDTRUTH_SUFFIX)
        old_groundtruth.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "source": old_statement.name,
                    "approvals": [
                        {
                            "transaction": {
                                "date": "2026-04-01",
                                "description_normalized": "test purchase",
                                "amount": "-12.34",
                                "currency": "EUR",
                                "occurrence": 1,
                            },
                            "required_documents": [
                                {"filename": "receipt.pdf", "hash_file": "doc12345"}
                            ],
                            "source_hint": {"statement_file": old_statement.name, "row": 9},
                        }
                    ],
                    "unmatched_file_approvals": [],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        with patch(
            "papertrail.commands.reconcile_single",
            return_value={
                "total": 0,
                "reconciled": 0,
                "unmatched": 0,
                "incomplete": 0,
                "unmatched_files": 0,
                "reconciliation_rate": 0.0,
                "matches": [],
            },
        ):
            _run_export_period(
                self.runtime,
                self.processed,
                self.export,
                "2026-04",
                export_file_config=self.runtime.profile.export,
                profile_context=None,
                merge_rules=[],
            )

        exported_statement = next((self.export / "2026-04").glob("*.xlsx"))
        restored_groundtruth = exported_statement.with_suffix(GROUNDTRUTH_SUFFIX)
        self.assertTrue(restored_groundtruth.exists())
        data = json.loads(restored_groundtruth.read_text(encoding="utf-8"))
        self.assertEqual(data["source"], exported_statement.name)
        self.assertEqual(data["approvals"][0]["required_documents"][0]["hash_file"], "doc12345")
        self.assertEqual(
            data["approvals"][0]["source_hint"]["statement_file"],
            exported_statement.name,
        )
        restored_receipts = list((self.export / "2026-04").glob("*doc12345.pdf"))
        self.assertEqual(len(restored_receipts), 1)
        self.assertNotIn("approved out-of-month", restored_receipts[0].name)
        self.assertTrue((self.export / "_reconciliation_groundtruth" / "2026-04.json").exists())

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

    def test_reconcile_filters_bank_notes_from_other_statement_bank(self):
        statement_path = self.export / "bpi-statement.xlsx"
        create_bpi_statement(
            statement_path,
            transactions=[
                {
                    "date_posting": "23-04-2026",
                    "date_value": "23-04-2026",
                    "description": "TRF SEPA+ INST 68 DE EXAMPLE COMPANY UNIP LDA",
                    "amount": 5000.00,
                    "currency": "EUR",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        bpi_bank_note = self.export / "bpi-bank-note.pdf"
        millennium_bank_note = self.export / "millennium-bank-note.pdf"
        create_pdf(bpi_bank_note, ["BPI transfer received"])
        create_pdf(millennium_bank_note, ["Millennium transfer debit"])

        self.repository.save_document(
            bpi_bank_note,
            self._metadata(
                "bpibank1",
                "bpibank1",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="Transferencia recebida",
                total_amount=5000.00,
            ),
        )
        self.repository.save_document(
            millennium_bank_note,
            self._metadata(
                "millbank",
                "millbank",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="millenniumbcp",
                issuing_party_raw="Banco Comercial Portugues, S.A.",
                document_title="Transferencia pontual a debito",
                total_amount=5000.00,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["matches"][0]["files"], ["bpi-bank-note.pdf"])

    def test_reconcile_requires_bnc_pair_for_cmp_on_non_bpi_statement(self):
        statement_path = self.export / "millennium-statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/04/2026",
            period_end="30/04/2026",
            transactions=[
                {
                    "date_posting": "23/04/2026",
                    "date_value": "23/04/2026",
                    "description": "IMP ABERT CRED EMPRES NR. LOANREF001",
                    "amount": -40.00,
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

        invoice_receipt = self.export / "CMP_2026-04-23 - invoice-receipt.pdf"
        create_pdf(invoice_receipt, ["Invoice receipt"])
        self.repository.save_document(
            invoice_receipt,
            self._metadata(
                "cmpdoc01",
                "cmpdoc01",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="invoice-receipt",
                document_type_raw="Fatura-Recibo",
                issuing_party="millenniumbcp",
                issuing_party_raw="Banco Comercial Portugues, S.A.",
                document_title="Imposto do Selo sobre Capital",
                total_amount=40.00,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 0)
        self.assertEqual(data["summary"]["incomplete"], 1)
        self.assertEqual(
            data["matches"][0]["errors"],
            [
                "missing bank-anchor (expected 1, found 0)",
                "missing BNC document for CMP/DIV support file",
            ],
        )

    def test_reconcile_pairs_cmp_support_file_with_matching_bnc_bank_note(self):
        statement_path = self.export / "millennium-statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/04/2026",
            period_end="30/04/2026",
            transactions=[
                {
                    "date_posting": "23/04/2026",
                    "date_value": "23/04/2026",
                    "description": "IMP ABERT CRED EMPRES NR. LOANREF001",
                    "amount": -40.00,
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

        bank_note = (
            self.export
            / (
                "BNC_2026-04-23 - bank-note - millennium-bcp - "
                "imp abert cred empres nr. LOANREF001 - 55fac9e8.pdf"
            )
        )
        invoice_receipt = (
            self.export
            / (
                "CMP_2026-04-23 - invoice-receipt - millenniumbcp - "
                "imposto do selo sobre capital - 749d9e69.pdf"
            )
        )
        create_pdf(bank_note, ["IMP ABERT CRED EMPRES NR. LOANREF001"])
        create_pdf(invoice_receipt, ["Imposto do Selo sobre Capital"])

        self.repository.save_document(
            bank_note,
            self._metadata(
                "bnc4000",
                "bnc4000",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="bank-note",
                document_type_raw="Movimento",
                issuing_party="millennium-bcp",
                issuing_party_raw="Banco Comercial Portugues, S.A.",
                document_title="IMP ABERT CRED EMPRES NR. LOANREF001",
                total_amount=40.00,
            ),
        )
        self.repository.save_document(
            invoice_receipt,
            self._metadata(
                "cmp4000",
                "cmp4000",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="invoice-receipt",
                document_type_raw="Fatura-Recibo",
                issuing_party="millenniumbcp",
                issuing_party_raw="Banco Comercial Portugues, S.A.",
                document_title="Imposto do Selo sobre Capital",
                total_amount=40.00,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["summary"]["unmatched_files"], 0)
        self.assertEqual(data["matches"][0]["errors"], [])
        self.assertEqual(
            sorted(data["matches"][0]["files"]),
            sorted([bank_note.name, invoice_receipt.name]),
        )

    def test_reconcile_requires_bank_anchor_for_bpi_loan_fee_statement(self):
        statement_path = self.export / "bpi-statement.xlsx"
        create_bpi_statement(
            statement_path,
            transactions=[
                {
                    "date_posting": "23-04-2026",
                    "date_value": "23-04-2026",
                    "description": "IMP ABERT CRED EMPRES NR. LOANREF001",
                    "amount": -40.00,
                    "currency": "EUR",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        invoice_receipt = self.export / "CMP_2026-04-23 - invoice-receipt.pdf"
        create_pdf(invoice_receipt, ["Invoice receipt"])
        self.repository.save_document(
            invoice_receipt,
            self._metadata(
                "cmpdoc02",
                "cmpdoc02",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="invoice-receipt",
                document_type_raw="Fatura-Recibo",
                issuing_party="millenniumbcp",
                issuing_party_raw="Banco Comercial Portugues, S.A.",
                document_title="Imposto do Selo sobre Capital",
                total_amount=40.00,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 0)
        self.assertEqual(data["summary"]["incomplete"], 1)
        self.assertEqual(
            data["matches"][0]["errors"],
            ["missing bank-anchor (expected 1, found 0)"],
        )

    def test_reconcile_rejects_unknown_bank_note_for_bpi_statement(self):
        statement_path = self.export / "bpi-statement.xlsx"
        create_bpi_statement(
            statement_path,
            transactions=[
                {
                    "date_posting": "23-04-2026",
                    "date_value": "23-04-2026",
                    "description": "TRF SEPA+ INST 68 DE EXAMPLE COMPANY UNIP LDA",
                    "amount": 5000.00,
                    "currency": "EUR",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        unknown_bank_note = self.export / "unknown-bank-note.pdf"
        create_pdf(unknown_bank_note, ["Unknown bank transfer"])
        self.repository.save_document(
            unknown_bank_note,
            self._metadata(
                "unkbank1",
                "unkbank1",
                date_created="2026-04-23",
                date_issued="2026-04-23",
                date_updated="2026-04-23",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="$UNKNOWN$",
                issuing_party_raw="$UNKNOWN$",
                document_title="Transferencia recebida",
                total_amount=5000.00,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 0)
        self.assertEqual(data["summary"]["unmatched"], 1)
        self.assertEqual(data["matches"], [])

    def test_reconcile_allows_multiple_bank_transfer_supporting_documents(self):
        statement_path = self.export / "bpi-statement.xlsx"
        create_bpi_statement(
            statement_path,
            transactions=[
                {
                    "date_posting": "23-04-2026",
                    "date_value": "23-04-2026",
                    "description": "TRF SEPA+ INST 69 DE EXAMPLE COMPANY UNIP LDA",
                    "amount": 5000.00,
                    "currency": "EUR",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        bank_note_path = self.export / "bpi-bank-note.pdf"
        bank_transfer_path = self.export / "bpi-bank-transfer.pdf"
        unrelated_bank_transfer_path = self.export / "unrelated-bpi-bank-transfer.pdf"
        aggregate_bank_transfer_path = self.export / "aggregate-bpi-bank-transfer.pdf"
        create_pdf(bank_note_path, ["BPI transfer received"])
        create_pdf(
            bank_transfer_path,
            [
                "\n".join(
                    [
                        "24/04/2026",
                        "Data de Emissao:",
                        "TRANSFERENCIAS RECEBIDAS",
                        "VALOR",
                        "DATA MOV",
                        "DATA VAL",
                        "5 000,00",
                        "TRANSFERENCIA RECEBIDA 69",
                        "23/04",
                        "23/04",
                        "EUR",
                    ]
                )
            ],
        )
        create_pdf(
            unrelated_bank_transfer_path,
            [
                "\n".join(
                    [
                        "02/04/2026",
                        "Data de Emissao:",
                        "TRANSFERENCIAS EMITIDAS",
                        "VALOR",
                        "DATA MOV",
                        "DATA VAL",
                        "5 000,00",
                        "TRF CR SEPA+ 64",
                        "01/04",
                        "01/04",
                        "EUR",
                    ]
                )
            ],
        )
        create_pdf(
            aggregate_bank_transfer_path,
            [
                "\n".join(
                    [
                        "01/04/2026",
                        "Data de Emissao:",
                        "TRANSFERENCIAS EMITIDAS",
                        "VALOR",
                        "DATA MOV",
                        "DATA VAL",
                        "5 000,00",
                        "TRF CR SEPA+ 63",
                        "31/03",
                        "31/03",
                        "EUR",
                    ]
                ),
                "\n".join(
                    [
                        "24/04/2026",
                        "Data de Emissao:",
                        "TRANSFERENCIAS RECEBIDAS",
                        "VALOR",
                        "DATA MOV",
                        "DATA VAL",
                        "5 000,00",
                        "TRANSFERENCIA RECEBIDA 69",
                        "23/04",
                        "23/04",
                        "EUR",
                    ]
                ),
            ],
        )

        self.repository.save_document(
            bank_note_path,
            self._metadata(
                "bpinote1",
                "bpinote1",
                date_created="2026-04-24",
                date_issued="2026-04-24",
                date_updated="2026-04-24",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="Transferencia recebida",
                total_amount=5000.00,
                page_count=1,
            ),
        )
        self.repository.save_document(
            bank_transfer_path,
            self._metadata(
                "bpitrf01",
                "bpitrf01",
                date_created="2026-04-02",
                date_issued="2026-04-02",
                date_updated="2026-04-02",
                document_type="bank-transfer",
                document_type_raw="Aviso de Lancamento",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="TRF CR SEPA+ Example Company",
                total_amount=5000.00,
                page_count=2,
            ),
        )
        self.repository.save_document(
            unrelated_bank_transfer_path,
            self._metadata(
                "bpitrf64",
                "bpitrf64",
                date_created="2026-04-02",
                date_issued="2026-04-02",
                date_updated="2026-04-02",
                document_type="bank-transfer",
                document_type_raw="Aviso de Lancamento",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="Transferencia emitida para Example Company",
                total_amount=5000.00,
                page_count=1,
            ),
        )
        self.repository.save_document(
            aggregate_bank_transfer_path,
            self._metadata(
                "bpibndl1",
                "bpibndl1",
                date_created="2026-04-24",
                date_issued="2026-04-24",
                date_updated="2026-04-24",
                document_type="bank-transfer",
                document_type_raw="Aviso de Lancamento",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="TRF CR SEPA+ Example Company",
                total_amount=5000.00,
                page_count=2,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(
            data["matches"][0]["files"],
            ["bpi-bank-note.pdf", "bpi-bank-transfer.pdf"],
        )
        unmatched_files = {entry["file"] for entry in data["unmatched_files"]}
        self.assertIn("unrelated-bpi-bank-transfer.pdf", unmatched_files)
        self.assertIn("aggregate-bpi-bank-transfer.pdf", unmatched_files)

    def test_reconcile_keeps_same_signature_bank_notes_when_rule_allows_multiple(self):
        statement_path = self.export / "millennium-statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/04/2026",
            period_end="30/04/2026",
            transactions=[
                {
                    "date_posting": "02/04/2026",
                    "date_value": "02/04/2026",
                    "description": "TRF. P/O EXAMPLE COMPANY, UNIPESSOAL, LDA",
                    "amount": 6100.00,
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

        outgoing_note = (
            self.export
            / (
                "BNC_2026-04-02 - bank-note - millenniumbcp - "
                "transferencia para puzzle message - 6d1a9c44.pdf"
            )
        )
        incoming_note = (
            self.export
            / (
                "BNC_2026-04-02 - bank-note - millenniumbcp - "
                "transferencia a credito - 05f9d9b0.pdf"
            )
        )
        create_pdf(outgoing_note, ["Transferencia para Example Company"])
        create_pdf(incoming_note, ["Transferencia a credito"])

        for path, hash_value, title in [
            (outgoing_note, "bnc6100a", "Transferencia para Example Company"),
            (incoming_note, "bnc6100b", "Transferencia a credito"),
        ]:
            self.repository.save_document(
                path,
                self._metadata(
                    hash_value,
                    hash_value,
                    date_created="2026-04-02",
                    date_issued="2026-04-02",
                    date_updated="2026-04-02",
                    document_type="bank-note",
                    document_type_raw="Movimento",
                    issuing_party="millenniumbcp",
                    issuing_party_raw="Banco Comercial Portugues, S.A.",
                    document_title=title,
                    total_amount=6100.00,
                    page_count=1,
                ),
            )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(
            sorted(data["matches"][0]["files"]),
            sorted([outgoing_note.name, incoming_note.name]),
        )

    def test_reconcile_rejects_bpi_bank_note_for_prior_month_transfer(self):
        statement_path = self.export / "bpi-statement.xlsx"
        create_bpi_statement(
            statement_path,
            transactions=[
                {
                    "date_posting": "01-04-2026",
                    "date_value": "01-04-2026",
                    "description": "TRF SEPA+ INST 66 DE EXAMPLE PERSON",
                    "amount": 5000.00,
                    "currency": "EUR",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        prior_month_bank_note_path = self.export / "prior-month-bpi-bank-note.pdf"
        bank_transfer_path = self.export / "april-bpi-bank-transfer.pdf"
        create_pdf(
            prior_month_bank_note_path,
            [
                "\n".join(
                    [
                        "01/04/2026",
                        "Data de Emissao:",
                        "VALOR",
                        "DATA MOV",
                        "DATA VAL",
                        "5 000,00",
                        "TRF SEPA+ INST 66 DE EXAMPLE PERSON",
                        "31/03",
                        "31/03",
                        "EUR",
                    ]
                )
            ],
        )
        create_pdf(
            bank_transfer_path,
            [
                "\n".join(
                    [
                        "02/04/2026",
                        "Data de Emissao:",
                        "TRANSFERENCIAS RECEBIDAS",
                        "VALOR",
                        "DATA MOV",
                        "DATA VAL",
                        "5 000,00",
                        "TRANSFERENCIA RECEBIDA 66",
                        "01/04",
                        "01/04",
                        "EUR",
                    ]
                )
            ],
        )

        self.repository.save_document(
            prior_month_bank_note_path,
            self._metadata(
                "bpinote2",
                "bpinote2",
                date_created="2026-04-01",
                date_issued="2026-04-01",
                date_updated="2026-04-01",
                document_type="bank-note",
                document_type_raw="Bank Note",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="Transferencia SEPA para Example Company",
                total_amount=5000.00,
                page_count=1,
            ),
        )
        self.repository.save_document(
            bank_transfer_path,
            self._metadata(
                "bpitrf66",
                "bpitrf66",
                date_created="2026-04-02",
                date_issued="2026-04-02",
                date_updated="2026-04-02",
                document_type="bank-transfer",
                document_type_raw="Aviso de Lancamento",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="Transferencia recebida",
                total_amount=5000.00,
                page_count=1,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["matches"][0]["files"], ["april-bpi-bank-transfer.pdf"])
        unmatched_files = {entry["file"] for entry in data["unmatched_files"]}
        self.assertIn("prior-month-bpi-bank-note.pdf", unmatched_files)

    def test_reconcile_rejects_bpi_acquisition_summary_for_stock_sale(self):
        statement_path = self.export / "bpi-statement.xlsx"
        create_bpi_statement(
            statement_path,
            transactions=[
                {
                    "date_posting": "01-04-2026",
                    "date_value": "01-04-2026",
                    "description": "TRANSFERENCIA A CREDITO LISTESTREFA",
                    "amount": 10565.32,
                    "currency": "EUR",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        acquisition_summary_path = self.export / "bpi-acquisition-summary.pdf"
        create_pdf(
            acquisition_summary_path,
            [
                "\n".join(
                    [
                        "Assunto: Mapa resumo de datas e valores de aquisicao de valores mobiliarios",
                        "Datas e Valores de Aquisicao dos Valores Mobiliarios detidos actualmente",
                        "TOTAL ANEXO J:",
                        "10 565,32",
                    ]
                )
            ],
        )
        self.repository.save_document(
            acquisition_summary_path,
            self._metadata(
                "bpiacq01",
                "bpiacq01",
                date_created="2026-04-01",
                date_issued="2026-04-01",
                date_updated="2026-04-01",
                document_type="bank-investment",
                document_type_raw="Mapa resumo de datas e valores de aquisicao de valores mobiliarios",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="Aquisicao de Valores Mobiliarios",
                total_amount=10565.32,
                page_count=2,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 0)
        self.assertEqual(data["summary"]["unmatched"], 1)
        self.assertEqual(data["matches"], [])
        self.assertEqual(
            data["unmatched_files"][0]["document_type"],
            "investment-acquisition-summary",
        )

    def test_reconcile_matches_bpi_stock_sale_from_usd_invoice_line_item(self):
        statement_path = self.export / "bpi-statement.xlsx"
        create_bpi_statement(
            statement_path,
            transactions=[
                {
                    "date_posting": "01-04-2026",
                    "date_value": "01-04-2026",
                    "description": "TRANSFERENCIA A CREDITO LISTESTREFA",
                    "amount": 10565.32,
                    "currency": "EUR",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        stock_invoice_path = self.export / "bpi-stock-fee-invoice.pdf"
        create_pdf(
            stock_invoice_path,
            [
                "\n".join(
                    [
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
                        "AO PREÇO DE: 123,000000 USD NA SESSÃO DE BOLSA: "
                        "31-03-2026 DA NASDAQ - ALL MARKETS",
                        "Nº ORDEM: V7538895",
                    ]
                )
            ],
        )
        self.repository.save_document(
            stock_invoice_path,
            self._metadata(
                "bpistockinvoice",
                "bpistockinvoice",
                date_created="2026-04-30",
                date_issued="2026-04-30",
                date_updated="2026-04-30",
                document_type="invoice",
                document_type_raw="FACTURA",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="Comissoes de conta e titulos",
                total_amount=63.08,
                page_count=2,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["summary"]["unmatched"], 0)
        self.assertEqual(data["matches"][0]["method"], "line-item")
        self.assertEqual(data["matches"][0]["files"], ["bpi-stock-fee-invoice.pdf"])
        self.assertEqual(data["matches"][0]["line_items"][0]["document_type"], "bank-stock-sell")
        self.assertEqual(data["matches"][0]["line_items"][0]["currency"], "USD")

    def test_reconcile_matches_multiple_bpi_stock_sales_from_columnar_invoice(self):
        statement_path = self.export / "bpi-statement.xlsx"
        create_bpi_statement(
            statement_path,
            transactions=[
                {
                    "date_posting": "24-03-2026",
                    "date_value": "24-03-2026",
                    "description": "TRANSFERENCIA A CREDITO LISTESTREFB",
                    "amount": 5318.66,
                    "currency": "EUR",
                },
                {
                    "date_posting": "31-03-2026",
                    "date_value": "31-03-2026",
                    "description": "TRANSFERENCIA A CREDITO LISTESTREFC",
                    "amount": 5008.82,
                    "currency": "EUR",
                },
            ],
        )

        from papertrail.bank_statement import classify_bank_statement

        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        stock_invoice_path = self.export / "bpi-stock-fee-invoice.pdf"
        create_pdf(
            stock_invoice_path,
            [
                "\n".join(
                    [
                        "FACTURA",
                        "Data de Emissão: 31-03-2026",
                        "Período de Facturação: 01-03-2026 a 31-03-2026",
                        "TÍTULOS",
                        "COMISSÃO CORRETAGEM",
                        "COMISSÃO CORRETAGEM",
                        "06/03",
                        "24/03",
                        "31/03",
                        "06/03",
                        "24/03",
                        "24/03",
                        "24/03",
                        "31/03",
                        "31/03",
                        "31/03",
                        "OPERAÇÃO BOLSA",
                        "TOTAL A CRÉDITO",
                        "OPERAÇÃO BOLSA",
                        "TOTAL A CRÉDITO",
                        "6 237,00",
                        "6 221,40",
                        "5 806,35",
                        "5 790,75",
                        "USD",
                        "USD",
                        "USD",
                        "USD",
                        "VENDA DE 45,0000 ACÇÕES STRATEGY INC(XNGS)",
                        "AO PREÇO DE: 138,600000 USD NA SESSÃO DE BOLSA: "
                        "23-03-2026 DA NASDAQ - ALL MARKETS",
                        "Nº ORDEM: V7448535",
                        "VENDA DE 45,0000 ACÇÕES STRATEGY INC(XNGS)",
                        "AO PREÇO DE: 129,030000 USD NA SESSÃO DE BOLSA: "
                        "30-03-2026 DA NASDAQ - ALL MARKETS",
                        "Nº ORDEM: V7521425",
                    ]
                )
            ],
        )
        self.repository.save_document(
            stock_invoice_path,
            self._metadata(
                "bpistockcolumns",
                "bpistockcolumns",
                date_created="2026-03-31",
                date_issued="2026-03-31",
                date_updated="2026-03-31",
                document_type="invoice",
                document_type_raw="FACTURA",
                issuing_party="bpi",
                issuing_party_raw="BPI",
                document_title="Comissoes de conta e titulos",
                total_amount=35.61,
                page_count=2,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 2)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["summary"]["unmatched"], 0)
        self.assertEqual([match["method"] for match in data["matches"]], ["line-item", "line-item"])
        self.assertEqual(
            [match["files"] for match in data["matches"]],
            [["bpi-stock-fee-invoice.pdf"], ["bpi-stock-fee-invoice.pdf"]],
        )
        self.assertEqual(
            [match["line_items"][0]["reference"] for match in data["matches"]],
            ["V7448535", "V7521425"],
        )
        self.assertEqual(
            [match["line_items"][0]["date"] for match in data["matches"]],
            ["2026-03-24", "2026-03-31"],
        )
        self.assertEqual(
            [match["line_items"][0]["amount"] for match in data["matches"]],
            [6221.4, 5790.75],
        )

    def test_reconcile_links_same_day_no_amount_contract_document(self):
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
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertEqual(data["summary"]["incomplete"], 0)
        self.assertEqual(data["matches"][0]["files"], ["shared-toll-bank-note.pdf"])
        self.assertEqual(data["matches"][0]["errors"], [])

    def test_reconcile_prunes_cross_month_bank_generated_candidates_when_same_month_exists(self):
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

    def test_reconcile_bank_fee_companion_invoice_without_bank_note_is_incomplete(self):
        statement_path = self.export / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/04/2026",
            period_end="30/04/2026",
            transactions=[
                {
                    "date_posting": "06/04/2026",
                    "date_value": "06/04/2026",
                    "description": "IMPOSTO SELO ART 17.3.4",
                    "amount": -0.60,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                },
                {
                    "date_posting": "06/04/2026",
                    "date_value": "06/04/2026",
                    "description": "COM.MAN.CONTA PACOTE M EMPRESA 032026",
                    "amount": -15.00,
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

        invoice_receipt = self.export / "package-fee-invoice-receipt.pdf"
        create_pdf(invoice_receipt, ["Package fee invoice receipt"])
        self.repository.save_document(
            invoice_receipt,
            self._metadata(
                "pkgfee00",
                "pkgfee00",
                date_created="2026-04-06",
                date_issued="2026-04-06",
                date_updated="2026-04-06",
                document_type="invoice-receipt",
                document_type_raw="Fatura-Recibo",
                issuing_party="MillenniumBCP",
                issuing_party_raw="Banco Comercial Portugues, S.A.",
                document_title="Man. Cta Pacote M Empresa",
                total_amount=15.60,
            ),
        )

        reconcile(self.runtime, self.export, dry_run=False)

        sidecar_path = statement_path.with_suffix(".reconciliation.json")
        data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 0)
        self.assertEqual(data["summary"]["unmatched"], 2)
        self.assertEqual(data["matches"], [])

    def test_reconcile_excludes_documents_reconciled_in_prior_month(self):
        mar_dir = self.export / "2026-03"
        apr_dir = self.export / "2026-04"
        mar_dir.mkdir()
        apr_dir.mkdir()

        from papertrail.bank_statement import classify_bank_statement

        march_statement = mar_dir / "statement.xlsx"
        create_millennium_statement(
            march_statement,
            period_start="01/03/2026",
            period_end="31/03/2026",
            transactions=[
                {
                    "date_posting": "31/03/2026",
                    "date_value": "31/03/2026",
                    "description": "STORE PAYMENT",
                    "amount": -12.34,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ],
        )
        march_statement_hash = hash_file_fast(march_statement)
        march_statement_metadata = classify_bank_statement(march_statement, march_statement_hash)
        self.repository.save_document(march_statement, march_statement_metadata)

        march_bank_note = mar_dir / "march-bank-note.pdf"
        march_receipt = mar_dir / "march-receipt.pdf"
        create_pdf(march_bank_note, ["March bank note"])
        create_pdf(march_receipt, ["March receipt"])
        self.repository.save_document(
            march_bank_note,
            self._metadata(
                "marbank0",
                "marbank0",
                date_created="2026-03-31",
                date_issued="2026-03-31",
                date_updated="2026-03-31",
                document_type="bank-note",
                document_type_raw="Bank Note",
                total_amount=12.34,
            ),
        )
        self.repository.save_document(
            march_receipt,
            self._metadata(
                "marrecv0",
                "marrecv0",
                date_created="2026-03-31",
                date_issued="2026-03-31",
                date_updated="2026-03-31",
                document_type="receipt",
                document_type_raw="Receipt",
                total_amount=12.34,
            ),
        )

        reconcile(self.runtime, mar_dir, dry_run=False)
        march_data = json.loads(
            march_statement.with_suffix(".reconciliation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(march_data["summary"]["reconciled"], 1)
        self.assertEqual(
            set(march_data["matches"][0]["files"]),
            {"march-bank-note.pdf", "march-receipt.pdf"},
        )

        april_statement = apr_dir / "statement.xlsx"
        create_millennium_statement(
            april_statement,
            period_start="01/04/2026",
            period_end="30/04/2026",
            transactions=[
                {
                    "date_posting": "01/04/2026",
                    "date_value": "01/04/2026",
                    "description": "STORE PAYMENT",
                    "amount": -12.34,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ],
        )
        april_statement_hash = hash_file_fast(april_statement)
        april_statement_metadata = classify_bank_statement(april_statement, april_statement_hash)
        self.repository.save_document(april_statement, april_statement_metadata)

        april_bank_note = apr_dir / "april-bank-note.pdf"
        create_pdf(april_bank_note, ["April bank note"])
        self.repository.save_document(
            april_bank_note,
            self._metadata(
                "aprbank0",
                "aprbank0",
                date_created="2026-04-01",
                date_issued="2026-04-01",
                date_updated="2026-04-01",
                document_type="bank-note",
                document_type_raw="Bank Note",
                total_amount=12.34,
            ),
        )

        reconcile(self.runtime, apr_dir, dry_run=False)

        april_data = json.loads(
            april_statement.with_suffix(".reconciliation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(april_data["summary"]["reconciled"], 0)
        self.assertEqual(april_data["summary"]["incomplete"], 1)
        self.assertEqual(april_data["matches"][0]["files"], ["april-bank-note.pdf"])
        self.assertNotIn(
            "march-receipt.pdf",
            {entry["file"] for entry in april_data["unmatched_files"]},
        )

    def test_reconcile_does_not_report_unmatched_supplemental_month_candidates(self):
        mar_dir = self.export / "2026-03"
        apr_dir = self.export / "2026-04"
        mar_dir.mkdir()
        apr_dir.mkdir()

        from papertrail.bank_statement import classify_bank_statement

        april_statement = apr_dir / "statement.xlsx"
        create_millennium_statement(
            april_statement,
            period_start="01/04/2026",
            period_end="30/04/2026",
            transactions=[
                {
                    "date_posting": "10/04/2026",
                    "date_value": "10/04/2026",
                    "description": "STORE PAYMENT",
                    "amount": -20.00,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ],
        )
        statement_hash = hash_file_fast(april_statement)
        statement_metadata = classify_bank_statement(april_statement, statement_hash)
        self.repository.save_document(april_statement, statement_metadata)

        april_bank_note = apr_dir / "april-bank-note.pdf"
        april_invoice = apr_dir / "april-invoice.pdf"
        april_extra = apr_dir / "april-extra.pdf"
        march_invoice = mar_dir / "march-old-invoice.pdf"
        create_pdf(april_bank_note, ["April bank note"])
        create_pdf(april_invoice, ["April invoice"])
        create_pdf(april_extra, ["April extra"])
        create_pdf(march_invoice, ["March invoice"])

        self.repository.save_document(
            april_bank_note,
            self._metadata(
                "aprbank2",
                "aprbank2",
                date_created="2026-04-10",
                date_issued="2026-04-10",
                date_updated="2026-04-10",
                document_type="bank-note",
                document_type_raw="Bank Note",
                total_amount=20.00,
            ),
        )
        self.repository.save_document(
            april_invoice,
            self._metadata(
                "aprinv20",
                "aprinv20",
                date_created="2026-04-10",
                date_issued="2026-04-10",
                date_updated="2026-04-10",
                document_type="invoice",
                document_type_raw="Invoice",
                issuing_party="Store",
                issuing_party_raw="Store",
                total_amount=20.00,
            ),
        )
        self.repository.save_document(
            april_extra,
            self._metadata(
                "aprxtra2",
                "aprxtra2",
                date_created="2026-04-10",
                date_issued="2026-04-10",
                date_updated="2026-04-10",
                document_type="receipt",
                document_type_raw="Receipt",
                issuing_party="Other Store",
                issuing_party_raw="Other Store",
                total_amount=99.00,
            ),
        )
        self.repository.save_document(
            march_invoice,
            self._metadata(
                "marinv20",
                "marinv20",
                date_created="2026-03-20",
                date_issued="2026-03-20",
                date_updated="2026-03-20",
                document_type="invoice",
                document_type_raw="Invoice",
                issuing_party="Store",
                issuing_party_raw="Store",
                total_amount=20.00,
            ),
        )

        reconcile(self.runtime, apr_dir, dry_run=False)

        data = json.loads(april_statement.with_suffix(".reconciliation.json").read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        unmatched_files = {entry["file"] for entry in data["unmatched_files"]}
        self.assertIn("april-extra.pdf", unmatched_files)
        self.assertNotIn("march-old-invoice.pdf", unmatched_files)

    def test_reconcile_does_not_report_out_of_month_primary_candidates_as_unmatched(self):
        mar_dir = self.export / "2026-03"
        mar_dir.mkdir()

        from papertrail.bank_statement import classify_bank_statement

        statement_path = mar_dir / "statement.xlsx"
        create_millennium_statement(
            statement_path,
            period_start="01/03/2026",
            period_end="31/03/2026",
            transactions=[
                {
                    "date_posting": "10/03/2026",
                    "date_value": "10/03/2026",
                    "description": "STORE PAYMENT",
                    "amount": -20.00,
                    "currency": "EUR",
                    "notes": "",
                    "treated": "Nao",
                }
            ],
        )
        statement_hash = hash_file_fast(statement_path)
        statement_metadata = classify_bank_statement(statement_path, statement_hash)
        self.repository.save_document(statement_path, statement_metadata)

        bank_note = mar_dir / "march-bank-note.pdf"
        invoice = mar_dir / "march-invoice.pdf"
        april_payroll = mar_dir / "april-payroll-social.pdf"
        create_pdf(bank_note, ["March bank note"])
        create_pdf(invoice, ["March invoice"])
        create_pdf(april_payroll, ["April social security document"])

        self.repository.save_document(
            bank_note,
            self._metadata(
                "marbn020",
                "marbn020",
                date_created="2026-03-10",
                date_issued="2026-03-10",
                date_updated="2026-03-10",
                document_type="bank-note",
                document_type_raw="Bank Note",
                total_amount=20.00,
            ),
        )
        self.repository.save_document(
            invoice,
            self._metadata(
                "marinv20",
                "marinv20",
                date_created="2026-03-10",
                date_issued="2026-03-10",
                date_updated="2026-03-10",
                document_type="invoice",
                document_type_raw="Invoice",
                issuing_party="Store",
                issuing_party_raw="Store",
                total_amount=20.00,
            ),
        )
        self.repository.save_document(
            april_payroll,
            self._metadata(
                "payapr08",
                "payapr08",
                date_created="2026-04-08",
                date_issued="2026-04-08",
                date_updated="2026-04-08",
                document_type="payroll-social",
                document_type_raw="Payroll Social",
                issuing_party="Seguranca Social",
                issuing_party_raw="Seguranca Social",
                document_title="Remuneracoes 2026-03",
                total_amount=596.83,
            ),
        )

        reconcile(self.runtime, mar_dir, dry_run=False)

        data = json.loads(statement_path.with_suffix(".reconciliation.json").read_text(encoding="utf-8"))
        self.assertEqual(data["summary"]["reconciled"], 1)
        self.assertNotIn(
            "april-payroll-social.pdf",
            {entry["file"] for entry in data["unmatched_files"]},
        )

    def test_reconcile_month_folder_uses_adjacent_month_candidates_selectively(self):
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
