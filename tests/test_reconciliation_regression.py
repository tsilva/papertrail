import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from papertrail.commands.reconcile import MatchResult, PDFCandidate, Transaction
from papertrail.reconciliation_groundtruth import (
    GROUNDTRUTH_SUFFIX,
    load_groundtruth,
    rows_with_transaction_keys,
    upsert_approval,
)
from papertrail.reconciliation_regression import (
    seed_missing_approvals,
    verify_reconciliation_regression,
)
from papertrail.repository import DocumentRepository
from tests.support import make_test_runtime


class ReconciliationRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.runtime = make_test_runtime(self.root)
        self.repository = DocumentRepository(self.runtime)
        self.export = self.runtime.paths.export

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write_document(self, name: str, *, hash_file: str, hash_content: str) -> Path:
        path = self.export / name
        path.write_bytes(b"pdf")
        path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "document_type": "invoice",
                    "issuing_party": "vendor",
                    "hash_file": hash_file,
                    "hash_content": hash_content,
                }
            ),
            encoding="utf-8",
        )
        return path

    def _write_statement(self) -> Path:
        statement = self.export / "statement.xlsx"
        statement.write_bytes(b"xlsx")
        statement.with_suffix(".json").write_text(
            json.dumps(
                {
                    "document_type": "bank-statement",
                    "issuing_party": "millennium-bcp",
                    "hash_file": "statement",
                    "hash_content": "statement",
                    "source_extension": ".xlsx",
                }
            ),
            encoding="utf-8",
        )
        return statement

    def test_seed_missing_approvals_from_current_reconciliation_without_duplicates(self):
        statement = self._write_statement()
        self._write_document("receipt-a.pdf", hash_file="file-a", hash_content="content-a")
        self._write_document("receipt-b.pdf", hash_file="file-b", hash_content="content-b")
        reconciliation = {
            "matches": [
                {
                    "row": 1,
                    "date": "2026-04-01",
                    "description": "A",
                    "amount": -1,
                    "currency": "EUR",
                    "files": ["receipt-a.pdf"],
                    "errors": [],
                },
                {
                    "row": 2,
                    "date": "2026-04-02",
                    "description": "B",
                    "amount": -2,
                    "currency": "EUR",
                    "files": ["receipt-b.pdf"],
                    "errors": [],
                },
            ],
            "unmatched": [],
        }
        statement.with_suffix(".reconciliation.json").write_text(
            json.dumps(reconciliation),
            encoding="utf-8",
        )
        first_row = rows_with_transaction_keys(reconciliation)[0]
        upsert_approval(
            statement.with_suffix(GROUNDTRUTH_SUFFIX),
            source=statement.name,
            row=first_row,
            documents=[{"filename": "receipt-a.pdf", "hash_file": "file-a"}],
        )

        self.assertEqual(seed_missing_approvals(self.runtime, self.repository, self.export), 1)
        self.assertEqual(seed_missing_approvals(self.runtime, self.repository, self.export), 0)
        groundtruth = load_groundtruth(statement.with_suffix(GROUNDTRUTH_SUFFIX))
        self.assertEqual(len(groundtruth["approvals"]), 2)

    def test_verify_fails_when_current_document_set_differs_from_approval(self):
        statement = self._write_statement()
        doc_path = self._write_document("receipt.pdf", hash_file="new-file", hash_content="new-content")
        row = rows_with_transaction_keys(
            {
                "matches": [
                    {
                        "row": 1,
                        "date": "2026-04-01",
                        "description": "A",
                        "amount": -1,
                        "currency": "EUR",
                        "files": ["receipt.pdf"],
                    }
                ],
                "unmatched": [],
            }
        )[0]
        upsert_approval(
            statement.with_suffix(GROUNDTRUTH_SUFFIX),
            source=statement.name,
            row=row,
            documents=[{"filename": "receipt.pdf", "hash_file": "old-file"}],
        )
        match = _match_for(statement_row=1, description="A", candidate_json=doc_path.with_suffix(".json"))

        with patch(
            "papertrail.reconciliation_regression.reconcile_single",
            return_value={"unmatched": 0, "incomplete": 0, "matches": [match]},
        ):
            result = verify_reconciliation_regression(self.runtime, self.repository, self.export)

        self.assertFalse(result.ok)
        self.assertTrue(any("document mismatch" in failure for failure in result.failures))

    def test_verify_handles_duplicate_transaction_occurrences(self):
        statement = self._write_statement()
        first_doc = self._write_document("first.pdf", hash_file="file-1", hash_content="content-1")
        second_doc = self._write_document("second.pdf", hash_file="file-2", hash_content="content-2")
        reconciliation = {
            "matches": [
                {
                    "row": 1,
                    "date": "2026-04-01",
                    "description": "DUP",
                    "amount": -1,
                    "currency": "EUR",
                    "files": ["first.pdf"],
                },
                {
                    "row": 2,
                    "date": "2026-04-01",
                    "description": "DUP",
                    "amount": -1,
                    "currency": "EUR",
                    "files": ["second.pdf"],
                },
            ],
            "unmatched": [],
        }
        rows = rows_with_transaction_keys(reconciliation)
        upsert_approval(
            statement.with_suffix(GROUNDTRUTH_SUFFIX),
            source=statement.name,
            row=rows[0],
            documents=[{"filename": "first.pdf", "hash_file": "file-1"}],
        )
        upsert_approval(
            statement.with_suffix(GROUNDTRUTH_SUFFIX),
            source=statement.name,
            row=rows[1],
            documents=[{"filename": "second.pdf", "hash_file": "file-2"}],
        )
        matches = [
            _match_for(statement_row=1, description="DUP", candidate_json=first_doc.with_suffix(".json")),
            _match_for(statement_row=2, description="DUP", candidate_json=second_doc.with_suffix(".json")),
        ]

        with patch(
            "papertrail.reconciliation_regression.reconcile_single",
            return_value={"unmatched": 0, "incomplete": 0, "matches": matches},
        ):
            result = verify_reconciliation_regression(self.runtime, self.repository, self.export)

        self.assertTrue(result.ok, result.failures)
        self.assertEqual(result.checked, 2)


def _match_for(statement_row: int, description: str, candidate_json: Path) -> MatchResult:
    return MatchResult(
        transaction=Transaction(
            row_number=statement_row,
            date_posting="2026-04-01",
            date_value="2026-04-01",
            description=description,
            amount=-1,
            currency="EUR",
            notes="",
            treated="",
        ),
        pdf_candidates=[
            PDFCandidate(
                json_path=candidate_json,
                pdf_filename=candidate_json.with_suffix(".pdf").name,
                date_issued="2026-04-01",
                document_type="invoice",
                document_type_raw="Invoice",
                document_title=None,
                issuing_party="vendor",
                total_amount=1,
                total_amount_currency="EUR",
                hash_file=None,
            )
        ],
        method="exact",
        confidence=1.0,
        reasoning="test",
    )


if __name__ == "__main__":
    unittest.main()
