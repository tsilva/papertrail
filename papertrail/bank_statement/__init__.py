"""Bank statement detection, parsing, and classification."""

from importlib import import_module

__all__ = [
    "BankFormat",
    "BankStatementData",
    "BankStatementParseError",
    "BankStatementReadError",
    "classify_bank_statement",
    "detect_bank_format",
    "get_parsers",
    "is_bank_statement",
    "load_transactions",
]

_EXPORTS = {
    "BankFormat": ("papertrail.bank_statement.models", "BankFormat"),
    "BankStatementData": ("papertrail.bank_statement.models", "BankStatementData"),
    "BankStatementParseError": ("papertrail.bank_statement.extractor", "BankStatementParseError"),
    "BankStatementReadError": ("papertrail.bank_statement.extractor", "BankStatementReadError"),
    "classify_bank_statement": ("papertrail.bank_statement.extractor", "classify_bank_statement"),
    "detect_bank_format": ("papertrail.bank_statement.extractor", "detect_bank_format"),
    "get_parsers": ("papertrail.bank_statement.extractor", "get_parsers"),
    "is_bank_statement": ("papertrail.bank_statement.extractor", "is_bank_statement"),
    "load_transactions": ("papertrail.bank_statement.extractor", "load_transactions"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
