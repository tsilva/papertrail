import json
import os
import tempfile
import unittest
from pathlib import Path

from papertrail.commands import copy_matching, reconcile
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


if __name__ == "__main__":
    unittest.main()
