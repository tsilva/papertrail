"""
Bank statement classification package for papertrail.

Deterministic classification of XLSX bank statements (no LLM needed).

Usage:
    from papertrail.bank_statement import classify_bank_statement, is_bank_statement

    if is_bank_statement(xlsx_path):
        metadata = classify_bank_statement(xlsx_path, file_hash)
"""

from papertrail.bank_statement.models import BankFormat, BankStatementData
from papertrail.bank_statement.extractor import (
    classify_bank_statement,
    detect_bank_format,
    is_bank_statement,
)

__all__ = [
    "BankFormat",
    "BankStatementData",
    "classify_bank_statement",
    "detect_bank_format",
    "is_bank_statement",
]
