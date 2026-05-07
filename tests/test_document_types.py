import unittest

from papertrail.document_types import normalize_document_type


class DocumentTypeTests(unittest.TestCase):
    def test_investment_acquisition_summary_is_not_generic_bank_investment(self):
        self.assertEqual(
            normalize_document_type(
                "bank-investment",
                "Mapa resumo de datas e valores de aquisicao de valores mobiliarios",
                "Aquisicao de Valores Mobiliarios",
            ),
            "investment-acquisition-summary",
        )

    def test_millennium_loan_disbursement_movement_is_bank_note(self):
        self.assertEqual(
            normalize_document_type(
                "bank-transfer",
                "TRF",
                "CONCESS CRED EMPR MN NR. LOANREF001",
            ),
            "bank-note",
        )


if __name__ == "__main__":
    unittest.main()
