import unittest
from pathlib import Path

from papertrail.commands.reconcile import (
    ReconciliationPolicy,
    MatchResult,
    PDFCandidate,
    Transaction,
    _ACTIVE_RECONCILIATION_POLICY,
    _candidate_sort_name,
    _filename_hash_key,
    _is_prior_reconciled_candidate,
    _link_evidence_counterparty_documents,
    _link_via_verde_period_documents,
    _reconciliation_rules,
)


class ReconcileLinkingTests(unittest.TestCase):
    def test_prior_reconciled_detection_survives_export_rename(self):
        old_filename = "BNC_2026-02-04 - bank-note - millenniumbcp - imposto selo art 17.3.4 - 52c84f00.pdf"
        candidate = PDFCandidate(
            json_path=Path("new-name.json"),
            pdf_filename="BNC_2026-02-04 - bank-note - millenniumbcp - 52c84f00.pdf",
            date_issued="2026-02-04",
            document_type="bank-note",
            document_type_raw="bank-note",
            document_title="Imposto selo art 17.3.4",
            issuing_party="millenniumbcp",
            total_amount=0.60,
            total_amount_currency="EUR",
            file_extension=".pdf",
            hash_file="52c84f00",
        )

        prior_keys = {old_filename, _filename_hash_key(old_filename)}

        self.assertIn("52c84f00", prior_keys)
        self.assertTrue(_is_prior_reconciled_candidate(candidate, prior_keys))

    def test_candidate_sort_name_prefers_source_filename(self):
        candidate = PDFCandidate(
            json_path=Path("new-name.json"),
            pdf_filename="BNC_2026-04-23 - bank-note - millenniumbcp - 594cdb99.pdf",
            date_issued="2026-04-23",
            document_type="bank-note",
            document_type_raw="bank-note",
            document_title="Transferencia pontual a debito SEPA+",
            issuing_party="millenniumbcp",
            total_amount=5000.0,
            total_amount_currency="EUR",
            file_extension=".pdf",
            hash_file="594cdb99",
            source_filename=(
                "2026-04-23 - bank-note - millenniumbcp - "
                "transferencia pontual a debito sepa+ - 5000 eur - 594cdb99.pdf"
            ),
        )

        self.assertEqual(_candidate_sort_name(candidate), candidate.source_filename)

    def test_unrelated_shared_bank_invoice_does_not_satisfy_supplier_evidence(self):
        txn = Transaction(
            row_number=9,
            date_posting="2026-06-30",
            date_value="2026-06-30",
            description="Ordem Pagamento s/Estrangeiro Ref.20260460130",
            amount=-364.80,
            currency="EUR",
            notes="",
            treated="",
        )
        bank_note = PDFCandidate(
            json_path=Path("coverflex-bank-note.json"),
            pdf_filename="BNC_2026-06-30 - bank-note - millennium.pdf",
            date_issued="2026-06-30",
            document_type="bank-note",
            document_type_raw="Nota de Lançamento",
            document_title="Ordem de Pagamento sobre o Estrangeiro",
            issuing_party="MillenniumBCP",
            total_amount=364.80,
            total_amount_currency="EUR",
            file_extension=".pdf",
            hash_file="bank0001",
            counterparty_id="millennium-bcp",
            is_bank_anchor=True,
        )
        unrelated_bank_invoice = PDFCandidate(
            json_path=Path("monthly-card-fees.json"),
            pdf_filename="CMP_2026-06-30 - invoice-receipt - millennium.pdf",
            date_issued="2026-06-30",
            document_type="invoice-receipt",
            document_type_raw="Fatura-Recibo",
            document_title="Operação: Cartões",
            issuing_party="MillenniumBCP",
            total_amount=0.72,
            total_amount_currency="EUR",
            file_extension=".pdf",
            hash_file="fees0001",
            counterparty_id="millennium-bcp",
            is_supplier_evidence=True,
            is_shared_period_document=True,
        )
        match = MatchResult(
            transaction=txn,
            pdf_candidates=[bank_note],
            method="exact",
            confidence=1.0,
            reasoning="Amount match",
        )

        token = _ACTIVE_RECONCILIATION_POLICY.set(
            ReconciliationPolicy(
                bank_counterparties=("millennium-bcp",),
                shared_period_transaction_keywords={"via-verde": ("VIAVERDE",)},
            )
        )
        try:
            evidence_ids = _link_evidence_counterparty_documents(
                [match],
                [unrelated_bank_invoice],
                _reconciliation_rules(),
            )
        finally:
            _ACTIVE_RECONCILIATION_POLICY.reset(token)

        self.assertEqual(evidence_ids, set())
        self.assertEqual(match.pdf_candidates, [bank_note])

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

        token = _ACTIVE_RECONCILIATION_POLICY.set(
            ReconciliationPolicy(
                shared_period_transaction_keywords={"shared-toll": ("SHAREDTOLL",)},
                shared_period_title_terms={"shared-toll": ("pagamentosdeservicos",)},
            )
        )
        try:
            updated_matches, updated_unmatched, shared_ids = _link_via_verde_period_documents(
                [match],
                [],
                [shared_parent],
                _reconciliation_rules(),
            )
        finally:
            _ACTIVE_RECONCILIATION_POLICY.reset(token)

        self.assertEqual(updated_unmatched, [])
        self.assertEqual(shared_ids, {shared_parent.candidate_id})
        self.assertEqual(updated_matches[0].pdf_candidates, [sub_document])


if __name__ == "__main__":
    unittest.main()
