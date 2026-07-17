import unittest
from unittest.mock import patch

from pydantic import ValidationError

from papertrail.bank_statement import bpi, millennium_bcp
from papertrail.bank_statement.models import parse_bank_amount
from papertrail.config import (
    _BUNDLED_RECONCILIATION_POLICY,
    BankStatementsSettings,
    ReconciliationSettings,
)


class BankStatementModelTests(unittest.TestCase):
    def test_bank_format_settings_are_sparse_parser_overrides(self):
        self.assertEqual(BankStatementsSettings().formats, {})

        override = {"millennium_bcp": {"header_row": 10}}
        self.assertEqual(BankStatementsSettings(formats=override).formats, override)

    def test_bank_parsers_own_complete_defaults(self):
        shared_keys = {
            "header_row",
            "data_start_row",
            "scan_columns",
            "expected_headers",
            "date_formats",
            "account_cell",
            "default_currency",
            "issuer_party",
            "issuer_party_raw",
            "max_columns",
            "description_column",
            "amount_column",
        }
        self.assertLessEqual(shared_keys, bpi.DEFAULT_CONFIG.keys())
        self.assertLessEqual(shared_keys, millennium_bcp.DEFAULT_CONFIG.keys())
        self.assertEqual(millennium_bcp.DEFAULT_CONFIG["max_columns"], 7)

    def test_parse_bank_amount_accepts_portuguese_thousands(self):
        cases = {
            "-4.932,62": -4932.62,
            "5.000,00": 5000.0,
            "-6.100,00": -6100.0,
            "10.565,32": 10565.32,
            "-11,06": -11.06,
        }

        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_bank_amount(raw), expected)

    def test_parse_bank_amount_keeps_plain_decimal_formats(self):
        self.assertEqual(parse_bank_amount("17.99"), 17.99)
        self.assertEqual(parse_bank_amount(17.99), 17.99)

    def test_reconciliation_policy_supplies_required_values(self):
        settings = ReconciliationSettings()
        self.assertEqual(
            settings.amount_tolerance,
            _BUNDLED_RECONCILIATION_POLICY["amount_tolerance"],
        )

        incomplete_policy = dict(_BUNDLED_RECONCILIATION_POLICY)
        incomplete_policy.pop("amount_tolerance")
        with (
            patch(
                "papertrail.config._BUNDLED_RECONCILIATION_POLICY",
                incomplete_policy,
            ),
            self.assertRaises(ValidationError),
        ):
            ReconciliationSettings()

    def test_reconciliation_builtin_rule_control_removes_bundled_rules(self):
        settings = ReconciliationSettings(include_builtin_rules=False)

        self.assertEqual(settings.rules, [])


if __name__ == "__main__":
    unittest.main()
