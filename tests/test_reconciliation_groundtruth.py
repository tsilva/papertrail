import json
import tempfile
import unittest
from pathlib import Path

from papertrail.reconciliation_groundtruth import (
    document_sets_match,
    groundtruth_path_for_document,
    load_groundtruth,
    remove_approval,
    remove_unmatched_file_approval,
    rows_with_transaction_keys,
    transaction_key_id,
    unmatched_file_approvals,
    upsert_approval,
    upsert_unmatched_file_approval,
)


class ReconciliationGroundtruthTests(unittest.TestCase):
    def test_transaction_key_uses_transaction_facts_not_row_number(self):
        recon_a = {
            "matches": [
                {
                    "row": 42,
                    "date": "2026-01-15",
                    "description": "PAGAMENTO  SERVIÇOS",
                    "amount": -18,
                    "currency": "eur",
                    "files": ["receipt.pdf"],
                }
            ],
            "unmatched": [],
        }
        recon_b = {
            "matches": [
                {
                    "row": 7,
                    "date": "2026-01-15",
                    "description": "pagamento servicos",
                    "amount": "-18.00",
                    "currency": "EUR",
                    "files": ["receipt.pdf"],
                }
            ],
            "unmatched": [],
        }

        key_a = rows_with_transaction_keys(recon_a)[0]["_transaction_key"]
        key_b = rows_with_transaction_keys(recon_b)[0]["_transaction_key"]

        self.assertEqual(transaction_key_id(key_a), transaction_key_id(key_b))

    def test_duplicate_transactions_get_occurrence_disambiguator(self):
        rows = rows_with_transaction_keys(
            {
                "matches": [
                    {
                        "row": 2,
                        "date": "2026-01-15",
                        "description": "FEE",
                        "amount": -1,
                        "currency": "EUR",
                        "files": ["b.pdf"],
                    },
                    {
                        "row": 1,
                        "date": "2026-01-15",
                        "description": "FEE",
                        "amount": -1,
                        "currency": "EUR",
                        "files": ["a.pdf"],
                    },
                ],
                "unmatched": [],
            }
        )

        self.assertEqual([row["_transaction_key"]["occurrence"] for row in rows], [1, 2])

    def test_document_matching_accepts_content_hash_fallback(self):
        self.assertTrue(
            document_sets_match(
                [{"filename": "new.pdf", "hash_file": "file2", "hash_content": "same"}],
                [{"filename": "old.pdf", "hash_file": "file1", "hash_content": "same"}],
            )
        )

    def test_upsert_and_remove_approval_persists_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            document_path = Path(tmp) / "statement.xlsx"
            groundtruth_path = groundtruth_path_for_document(document_path)
            row = rows_with_transaction_keys(
                {
                    "matches": [
                        {
                            "row": 9,
                            "date": "2026-01-01",
                            "description": "TEST PURCHASE",
                            "amount": -12.34,
                            "currency": "EUR",
                            "files": ["invoice.pdf"],
                        }
                    ],
                    "unmatched": [],
                }
            )[0]

            upsert_approval(
                groundtruth_path,
                source=document_path.name,
                row=row,
                documents=[
                    {
                        "filename": "invoice.pdf",
                        "hash_file": "file1111",
                        "hash_content": "hash1111",
                    }
                ],
            )

            data = json.loads(groundtruth_path.read_text(encoding="utf-8"))
            self.assertEqual(data["source"], "statement.xlsx")
            self.assertEqual(len(data["approvals"]), 1)
            self.assertEqual(
                load_groundtruth(groundtruth_path)["approvals"][0]["required_documents"][0]["hash_file"],
                "file1111",
            )

            self.assertTrue(remove_approval(groundtruth_path, row=row))
            self.assertEqual(load_groundtruth(groundtruth_path)["approvals"], [])

    def test_unmatched_file_approval_persists_expected_unreconciled_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            document_path = Path(tmp) / "statement.xlsx"
            groundtruth_path = groundtruth_path_for_document(document_path)
            document = {
                "filename": "invoice.pdf",
                "hash_file": "file1111",
                "hash_content": "hash1111",
            }

            approval = upsert_unmatched_file_approval(
                groundtruth_path,
                source=document_path.name,
                document=document,
            )

            self.assertEqual(approval["status"], "expected_unreconciled")
            data = load_groundtruth(groundtruth_path)
            self.assertEqual(data["source"], "statement.xlsx")
            self.assertEqual(len(unmatched_file_approvals(data)), 1)
            self.assertEqual(
                unmatched_file_approvals(data)[0]["document"]["hash_file"],
                "file1111",
            )

            upsert_unmatched_file_approval(
                groundtruth_path,
                source=document_path.name,
                document=dict(document, filename="renamed.pdf"),
            )
            self.assertEqual(
                len(load_groundtruth(groundtruth_path)["unmatched_file_approvals"]),
                1,
            )

            self.assertTrue(
                remove_unmatched_file_approval(
                    groundtruth_path,
                    document={"filename": "new.pdf", "hash_content": "hash1111"},
                )
            )
            self.assertEqual(load_groundtruth(groundtruth_path)["unmatched_file_approvals"], [])
