import unittest
from pathlib import Path

from papertrail.commands.reconcile import (
    MatchResult,
    PDFCandidate,
    Transaction,
    _link_via_verde_period_documents,
    _reconciliation_rules,
)


class ReconcileLinkingTests(unittest.TestCase):
    def test_via_verde_shared_period_link_does_not_duplicate_existing_file(self):
        txn = Transaction(
            row_number=28,
            date_posting="2026-03-09",
            date_value="2026-03-09",
            description="MDB TESTREF PAG BX VAL-SHAREDTOLL MOV 18",
            amount=-2.40,
            currency="EUR",
            notes="",
            treated="",
        )
        sub_document = PDFCandidate(
            json_path=Path("shared-toll.json"),
            pdf_filename="CMP_2026-03-31 - invoice-receipt - via verde.pdf",
            date_issued="2026-03-31",
            document_type="invoice",
            document_type_raw="Invoice",
            document_title=None,
            issuing_party="Banco Comercial Português, S.A.",
            total_amount=2.40,
            total_amount_currency="EUR",
            file_extension=".pdf",
            sub_doc_index=4,
            is_sub_document=True,
        )
        shared_parent = PDFCandidate(
            json_path=Path("shared-toll.json"),
            pdf_filename=sub_document.pdf_filename,
            date_issued="2026-03-31",
            document_type="invoice-receipt",
            document_type_raw="Invoice Receipt",
            document_title="Pagamentos de Serviços",
            issuing_party="Shared Toll",
            total_amount=34.14,
            total_amount_currency="EUR",
            file_extension=".pdf",
            hash_file="616d0d66",
            counterparty_id="shared-toll",
            is_shared_period_document=True,
        )
        match = MatchResult(
            transaction=txn,
            pdf_candidates=[sub_document],
            method="exact",
            confidence=1.0,
            reasoning="Amount match",
        )

        updated_matches, updated_unmatched, shared_ids = _link_via_verde_period_documents(
            [match],
            [],
            [shared_parent],
            _reconciliation_rules(),
        )

        self.assertEqual(updated_unmatched, [])
        self.assertEqual(shared_ids, {shared_parent.candidate_id})
        self.assertEqual(updated_matches[0].pdf_candidates, [sub_document])


if __name__ == "__main__":
    unittest.main()
