import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from papertrail.commands.reconcile import _policy_from_profile, _rules_from_profile
from papertrail.config import load_profile
from papertrail.rules import RuleEngine
from tests.support import make_test_runtime


class RuleEngineTests(unittest.TestCase):
    def test_match_value_supports_numeric_operators(self):
        engine = RuleEngine()
        self.assertTrue(engine.match_value(12, ">=10"))
        self.assertTrue(engine.match_value(4, "<5"))
        self.assertFalse(engine.match_value(4, ">5"))
        self.assertTrue(engine.match_value(False, False))
        self.assertTrue(engine.match_value(True, "true"))

    def test_match_value_supports_wildcards_and_profile_variables(self):
        engine = RuleEngine(profile_context={"tax_number": "TESTOWNER"})
        self.assertTrue(engine.match_value("bank-note", "bank-*"))
        self.assertEqual(engine.resolve_profile_value("${profile.tax_number}"), "TESTOWNER")

    def test_match_doc_type_supports_pipe_patterns(self):
        engine = RuleEngine()
        self.assertTrue(engine.match_doc_type("invoice-credit", "invoice|invoice-credit"))
        self.assertTrue(engine.match_doc_type("bank-note-extra", "bank-*"))
        self.assertFalse(engine.match_doc_type("receipt", "invoice|bank-*"))

    def test_match_doc_type_treats_card_transactions_as_bank_notes(self):
        engine = RuleEngine()
        self.assertTrue(engine.match_doc_type("bank-card-transaction", "bank-note"))

    def test_match_doc_type_uses_configured_document_families(self):
        engine = RuleEngine(
            document_families={
                "bank_anchor": {"aliases": ["bank-anchor"], "types": ["custom-bank-doc"]},
            }
        )
        self.assertTrue(engine.match_doc_type("custom-bank-doc", "bank-anchor"))

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

    def test_has_qrcode_matches_parent_and_sub_documents(self):
        engine = RuleEngine()
        self.assertFalse(
            engine.get_nested_value({"qrcode": None, "sub_documents": None}, "has_qrcode")
        )
        self.assertTrue(
            engine.get_nested_value(
                {"qrcode": {"qr_type": "portuguese_invoice"}}, "has_qrcode"
            )
        )
        self.assertTrue(
            engine.get_nested_value(
                {
                    "qrcode": None,
                    "sub_documents": [{"qrcode": {"qr_type": "portuguese_invoice"}}],
                },
                "has_qrcode",
            )
        )

    def test_export_prefix_can_distinguish_via_verde_without_qr(self):
        file_mappings = SimpleNamespace(
            rules=[
                SimpleNamespace(
                    match={
                        "document_type": "invoice*",
                        "issuing_party": "shared-toll",
                        "has_qrcode": False,
                    },
                    prefix="EXC_",
                ),
                SimpleNamespace(match={"document_type": "invoice*"}, prefix="CMP_"),
            ],
            default_prefix="DIV_",
        )
        engine = RuleEngine()
        base = {"document_type": "invoice", "issuing_party": "shared-toll"}
        self.assertEqual(
            engine.evaluate_export_prefix(
                {**base, "qrcode": None, "sub_documents": None},
                file_mappings=file_mappings,
            ),
            "EXC_",
        )
        self.assertEqual(
            engine.evaluate_export_prefix(
                {**base, "qrcode": {"qr_type": "portuguese_invoice"}},
                file_mappings=file_mappings,
            ),
            "CMP_",
        )

    def test_export_prefix_matches_slug_pattern_against_display_value(self):
        file_mappings = SimpleNamespace(
            rules=[
                SimpleNamespace(
                    match={
                        "document_type": "invoice*",
                        "issuing_party": "shared-toll",
                        "has_qrcode": False,
                    },
                    prefix="EXC_",
                ),
                SimpleNamespace(match={"document_type": "invoice*"}, prefix="CMP_"),
            ],
            default_prefix="DIV_",
        )
        metadata = {
            "document_type": "invoice",
            "issuing_party": "Shared Toll",
            "qrcode": None,
            "sub_documents": None,
        }

        self.assertEqual(
            RuleEngine().evaluate_export_prefix(metadata, file_mappings=file_mappings),
            "EXC_",
        )

    def test_export_prefix_excludes_investment_key_information_documents(self):
        file_mappings = SimpleNamespace(
            rules=[
                SimpleNamespace(
                    match={"document_type": "investment-key-information-document"},
                    prefix="EXC_",
                ),
            ],
            default_prefix="DIV_",
        )
        engine = RuleEngine()
        metadata = {"document_type": "investment-key-information-document"}
        self.assertEqual(engine.evaluate_export_prefix(metadata, file_mappings=file_mappings), "EXC_")

    def test_classify_transaction_uses_first_matching_rule(self):
        rules = [
            SimpleNamespace(name="credit", direction="credit", match_description=[]),
            SimpleNamespace(name="debit", direction="debit", match_description=[]),
        ]
        txn = SimpleNamespace(description="TRANSFER", amount=5)
        engine = RuleEngine()
        self.assertEqual(engine.classify_transaction(txn, rules)[0], "credit")

    def test_validate_match_accepts_multiple_expected_page_counts(self):
        rule = SimpleNamespace(
            name="fee",
            direction=None,
            match_description=[],
            required_types={"invoice": 1},
            shared_types={},
            expected_page_count={"invoice": [1, 2]},
        )
        txn = SimpleNamespace(description="FEE", amount=-1)
        candidate = SimpleNamespace(
            effective_document_type="invoice",
            document_type="invoice",
            pdf_filename="invoice.pdf",
            page_count=2,
        )
        match = SimpleNamespace(transaction=txn, pdf_candidates=[candidate])
        self.assertEqual(RuleEngine().validate_match(match, [rule]), [])

    def test_reconciliation_policy_and_rules_can_come_from_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = make_test_runtime(Path(tmpdir))
            runtime.profile.reconciliation.amount_tolerance = 0.05
            runtime.profile.reconciliation.bank_export_prefix = "BNK_"
            runtime.profile.reconciliation.rules = [
                {
                    "name": "custom-debit",
                    "direction": "debit",
                    "required_types": {"supplier-evidence": 1},
                }
            ]

            policy = _policy_from_profile(runtime.profile)
            rules = _rules_from_profile(runtime.profile)

        self.assertEqual(policy.amount_tolerance, 0.05)
        self.assertEqual(policy.bank_export_prefix, "BNK_")
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0].name, "custom-debit")

    def test_reconciliation_policy_files_merge_into_profile(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            profile_dir = root / "profiles" / "custom"
            profile_dir.mkdir(parents=True)
            policy_path = profile_dir / "policy.yaml"
            policy_path.write_text(
                "\n".join(
                    [
                        "amount_tolerance: 0.05",
                        "bank_export_prefix: BNK_",
                        "document_families:",
                        "  bank_anchor:",
                        "    aliases: [bank-anchor]",
                        "    types: [custom-bank-doc]",
                    ]
                ),
                encoding="utf-8",
            )
            (profile_dir / "profile.yaml").write_text(
                "\n".join(
                    [
                        "profile:",
                        "  name: custom",
                        "paths: {}",
                        "reconciliation:",
                        "  policy_files: [policy.yaml]",
                        "  amount_tolerance: 0.07",
                    ]
                ),
                encoding="utf-8",
            )

            with patch.dict("os.environ", {"PAPERTRAIL_HOME": str(root)}):
                profile = load_profile("custom")

        self.assertEqual(profile.reconciliation.amount_tolerance, 0.07)
        self.assertEqual(profile.reconciliation.bank_export_prefix, "BNK_")
        self.assertEqual(
            profile.reconciliation.document_families["bank_anchor"]["types"],
            ["custom-bank-doc"],
        )


if __name__ == "__main__":
    unittest.main()
