import unittest

from papertrail.reconciliation_evidence import (
    BANK_ANCHOR,
    CONTRACT_EVIDENCE,
    IGNORE,
    INVESTMENT_EVIDENCE,
    PAYROLL_EVIDENCE,
    SUPPLIER_EVIDENCE,
    TAX_EVIDENCE,
    build_document_evidence,
    counterparty_id,
    document_family_for_type,
    document_type_matches_family,
)


class ReconciliationEvidenceTests(unittest.TestCase):
    def test_maps_april_document_types_to_families(self):
        cases = {
            "bank-note": BANK_ANCHOR,
            "bank-transfer": BANK_ANCHOR,
            "bank-card-transaction": BANK_ANCHOR,
            "invoice": SUPPLIER_EVIDENCE,
            "receipt": SUPPLIER_EVIDENCE,
            "invoice-receipt": SUPPLIER_EVIDENCE,
            "tax-irs": TAX_EVIDENCE,
            "payroll-social": PAYROLL_EVIDENCE,
            "payroll-salary": PAYROLL_EVIDENCE,
            "bank-investment": INVESTMENT_EVIDENCE,
            "investment-acquisition-summary": INVESTMENT_EVIDENCE,
            "bank-stock-sell": INVESTMENT_EVIDENCE,
            "contract-signup": CONTRACT_EVIDENCE,
            "investment-key-information-document": IGNORE,
            "loan-simulation": IGNORE,
        }
        for doc_type, expected in cases.items():
            with self.subTest(doc_type=doc_type):
                self.assertEqual(document_family_for_type(doc_type), expected)

    def test_zero_amount_supplier_documents_are_ignored(self):
        self.assertEqual(
            document_family_for_type("invoice", {"total_amount": 0}),
            IGNORE,
        )
        self.assertEqual(
            document_family_for_type("receipt", {"total_amount": "0.00"}),
            IGNORE,
        )

    def test_counterparty_aliases_collapse_bank_drift(self):
        self.assertEqual(counterparty_id({"issuing_party": "MillenniumBCP"}), "millennium-bcp")
        self.assertEqual(counterparty_id({"issuing_party": "millenniumbcp"}), "millennium-bcp")
        self.assertEqual(counterparty_id({"issuing_party_raw": "BANCO BPI S.A."}), "bpi")

    def test_counterparty_aliases_use_tax_number_when_known(self):
        self.assertEqual(counterparty_id({"issuer_tax_number": "TESTSHAREDTOLL"}), "shared-toll")
        self.assertEqual(counterparty_id({"issuer_tax_number": "TESTCLOUDVAT"}), "google")
        self.assertEqual(counterparty_id({"issuer_tax_number": "TESTUNKNOWN"}), "tax:PTTESTUNKNOWN")

    def test_document_type_matches_family_aliases(self):
        self.assertTrue(document_type_matches_family("invoice-receipt", "supplier-evidence"))
        self.assertTrue(document_type_matches_family("bank-transfer", "bank-anchor"))
        self.assertFalse(document_type_matches_family("invoice", "bank-anchor"))

    def test_build_document_evidence_flags_shared_period_docs(self):
        evidence = build_document_evidence(
            {
                "document_type": "invoice-receipt",
                "issuing_party": "Shared Toll",
                "document_title": "Pagamentos de Serviços Shared Toll",
            }
        )
        self.assertEqual(evidence.document_family, SUPPLIER_EVIDENCE)
        self.assertEqual(evidence.counterparty_id, "shared-toll")
        self.assertTrue(evidence.is_shared_period_document)


if __name__ == "__main__":
    unittest.main()
