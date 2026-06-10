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

PROFILE_COUNTERPARTY_ALIASES = {
    "TESTSHAREDTOLL": "shared-toll",
    "TESTCLOUDVAT": "cloud-vendor",
    "TESTINSURER": "insurance-provider",
    "Shared Toll": "shared-toll",
}


class ReconciliationEvidenceTests(unittest.TestCase):
    def test_maps_april_document_types_to_families(self):
        cases = {
            "bank-note": BANK_ANCHOR,
            "bank-transfer": BANK_ANCHOR,
            "bank-card-transaction": BANK_ANCHOR,
            "invoice": SUPPLIER_EVIDENCE,
            "receipt": SUPPLIER_EVIDENCE,
            "invoice-receipt": SUPPLIER_EVIDENCE,
            "statement-receipt": SUPPLIER_EVIDENCE,
            "insurance-notice": SUPPLIER_EVIDENCE,
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
        aliases = {
            "Bank Alpha": "bank-alpha",
            "bankalpha": "bank-alpha",
            "BANK BETA S.A.": "bank-beta",
        }
        self.assertEqual(
            counterparty_id({"issuing_party": "Bank Alpha"}, counterparty_aliases=aliases),
            "bank-alpha",
        )
        self.assertEqual(
            counterparty_id({"issuing_party": "bankalpha"}, counterparty_aliases=aliases),
            "bank-alpha",
        )
        self.assertEqual(
            counterparty_id({"issuing_party_raw": "BANK BETA S.A."}, counterparty_aliases=aliases),
            "bank-beta",
        )

    def test_counterparty_aliases_use_tax_number_when_known(self):
        self.assertEqual(
            counterparty_id(
                {"issuer_tax_number": "TESTSHAREDTOLL"},
                counterparty_aliases=PROFILE_COUNTERPARTY_ALIASES,
            ),
            "shared-toll",
        )
        self.assertEqual(
            counterparty_id(
                {"issuer_tax_number": "TESTCLOUDVAT"},
                counterparty_aliases=PROFILE_COUNTERPARTY_ALIASES,
            ),
            "cloud-vendor",
        )
        self.assertEqual(
            counterparty_id(
                {"issuer_tax_number": "TESTINSURER"},
                counterparty_aliases=PROFILE_COUNTERPARTY_ALIASES,
            ),
            "insurance-provider",
        )
        self.assertEqual(counterparty_id({"issuer_tax_number": "TESTUNKNOWN"}), "tax:TESTUNKNOWN")

    def test_counterparty_tax_prefix_can_be_disabled(self):
        self.assertEqual(
            counterparty_id(
                {"issuer_tax_number": "TESTUNKNOWN"},
                counterparty_aliases={},
                tax_number_default_country_prefix="",
            ),
            "tax:TESTUNKNOWN",
        )

    def test_counterparty_aliases_can_be_extended_by_policy(self):
        self.assertEqual(
            counterparty_id(
                {"issuing_party": "Acme, Lda."},
                counterparty_aliases={"Acme Lda": "acme"},
            ),
            "acme",
        )

    def test_document_type_matches_family_aliases(self):
        self.assertTrue(document_type_matches_family("invoice-receipt", "supplier-evidence"))
        self.assertTrue(document_type_matches_family("bank-transfer", "bank-anchor"))
        self.assertFalse(document_type_matches_family("invoice", "bank-anchor"))

    def test_document_families_can_be_overridden_by_policy(self):
        families = {
            "bank_anchor": {"aliases": ["bank-anchor"], "types": ["custom-bank-doc"]},
            "supplier_evidence": {
                "aliases": ["supplier-evidence"],
                "prefixes": ["bill-"],
                "ignore_when_zero_amount": True,
            },
            "ignore": {"types": ["ignore-me"]},
        }

        self.assertEqual(
            document_family_for_type("custom-bank-doc", document_families=families),
            BANK_ANCHOR,
        )
        self.assertEqual(
            document_family_for_type("bill-monthly", document_families=families),
            SUPPLIER_EVIDENCE,
        )
        self.assertEqual(
            document_family_for_type(
                "bill-monthly",
                {"total_amount": 0},
                document_families=families,
            ),
            IGNORE,
        )
        self.assertTrue(
            document_type_matches_family(
                "custom-bank-doc",
                "bank-anchor",
                document_families=families,
            )
        )

    def test_build_document_evidence_flags_shared_period_docs(self):
        evidence = build_document_evidence(
            {
                "document_type": "invoice-receipt",
                "issuing_party": "Shared Toll",
                "document_title": "Pagamentos de Serviços Shared Toll",
            },
            counterparty_aliases=PROFILE_COUNTERPARTY_ALIASES,
            shared_period_title_terms={"shared-toll": ["pagamentosdeservicos"]},
        )
        self.assertEqual(evidence.document_family, SUPPLIER_EVIDENCE)
        self.assertEqual(evidence.counterparty_id, "shared-toll")
        self.assertTrue(evidence.is_shared_period_document)

    def test_statement_receipt_can_be_shared_period_supplier_evidence(self):
        evidence = build_document_evidence(
            {
                "document_type": "statement-receipt",
                "issuer_tax_number": "TESTSHAREDTOLL",
                "issuing_party": "Shared Toll",
                "document_title": "Pagamentos de Serviços Shared Toll",
            },
            counterparty_aliases=PROFILE_COUNTERPARTY_ALIASES,
            shared_period_title_terms={"shared-toll": ["pagamentosdeservicos"]},
        )
        self.assertEqual(evidence.document_family, SUPPLIER_EVIDENCE)
        self.assertEqual(evidence.counterparty_id, "shared-toll")
        self.assertTrue(evidence.is_shared_period_document)


if __name__ == "__main__":
    unittest.main()
