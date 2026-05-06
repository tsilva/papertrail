import unittest

from papertrail.bank_statement.models import parse_bank_amount


class BankStatementModelTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
