import unittest
from types import SimpleNamespace

from papertrail.rules import RuleEngine


class RuleEngineTests(unittest.TestCase):
    def test_match_value_supports_numeric_operators(self):
        engine = RuleEngine()
        self.assertTrue(engine.match_value(12, ">=10"))
        self.assertTrue(engine.match_value(4, "<5"))
        self.assertFalse(engine.match_value(4, ">5"))

    def test_match_value_supports_wildcards_and_profile_variables(self):
        engine = RuleEngine(profile_context={"tax_number": "TESTOWNER"})
        self.assertTrue(engine.match_value("bank-note", "bank-*"))
        self.assertEqual(engine.resolve_profile_value("${profile.tax_number}"), "TESTOWNER")

    def test_match_doc_type_supports_pipe_patterns(self):
        engine = RuleEngine()
        self.assertTrue(engine.match_doc_type("invoice-credit", "invoice|invoice-credit"))
        self.assertTrue(engine.match_doc_type("bank-note-extra", "bank-*"))
        self.assertFalse(engine.match_doc_type("receipt", "invoice|bank-*"))

    def test_evaluate_export_prefix_uses_first_match(self):
        file_mappings = SimpleNamespace(
            rules=[
                SimpleNamespace(match={"document_type": "invoice", "issuer_tax_number": "${profile.tax_number}"}, prefix="VND_"),
                SimpleNamespace(match={"document_type": "invoice"}, prefix="CMP_"),
            ],
            default_prefix="DIV_",
        )
        engine = RuleEngine(profile_context={"tax_number": "TESTOWNER"})
        metadata = {"document_type": "invoice", "issuer_tax_number": "TESTOWNER"}
        self.assertEqual(engine.evaluate_export_prefix(metadata, file_mappings=file_mappings), "VND_")

    def test_classify_transaction_uses_first_matching_rule(self):
        rules = [
            SimpleNamespace(name="credit", direction="credit", match_description=[]),
            SimpleNamespace(name="debit", direction="debit", match_description=[]),
        ]
        txn = SimpleNamespace(description="TRANSFER", amount=5)
        engine = RuleEngine()
        self.assertEqual(engine.classify_transaction(txn, rules)[0], "credit")


if __name__ == "__main__":
    unittest.main()
