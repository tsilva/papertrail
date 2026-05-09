"""Bank statement format detection and classification dispatcher."""

import warnings
from datetime import datetime
from pathlib import Path

import openpyxl

from papertrail.bank_statement.models import (
    BankFormat,
    BankStatementParser,
    BankTransactionRecord,
)
from papertrail.logging_utils import get_logger
from papertrail.models import DocumentMetadata

from . import bpi, millennium_bcp

logger = get_logger("bank_statement")

_PARSERS = [millennium_bcp, bpi]


class BankStatementReadError(RuntimeError):
    """Raised when an XLSX workbook cannot be opened for bank statement parsing."""


class BankStatementParseError(RuntimeError):
    """Raised when a recognized bank statement cannot be parsed."""


def get_parsers() -> tuple[BankStatementParser, ...]:
    return tuple(_PARSERS)


def _detect_parser(
    xlsx_path: Path,
    *,
    raise_on_open_error: bool = False,
    settings: object | None = None,
) -> BankStatementParser | None:
    try:
        workbook = openpyxl.load_workbook(xlsx_path, data_only=True)
        worksheet = workbook.active
    except Exception as exc:
        logger.debug(f"Could not open {xlsx_path.name}: {exc}")
        if raise_on_open_error:
            raise BankStatementReadError(
                f"Could not open XLSX workbook {xlsx_path.name}: {exc}"
            ) from exc
        return None

    try:
        for parser in _PARSERS:
            if parser.can_parse(worksheet, config=_parser_config(settings, parser)):
                return parser
    finally:
        workbook.close()

    return None


def _parser_config(settings: object | None, parser: BankStatementParser) -> dict[str, object]:
    formats = getattr(settings, "formats", None)
    if isinstance(formats, dict):
        value = formats.get(parser.FORMAT.value, {})
        if isinstance(value, dict):
            return dict(value)
    return {}


def detect_bank_format(xlsx_path: Path, *, settings: object | None = None) -> BankFormat | None:
    """Detect bank statement format by trying each parser."""
    parser = _detect_parser(xlsx_path, settings=settings)
    return parser.FORMAT if parser is not None else None


def is_bank_statement(xlsx_path: Path, *, settings: object | None = None) -> bool:
    """Check if an XLSX file is a recognized bank statement."""
    return detect_bank_format(xlsx_path, settings=settings) is not None


def load_transactions(
    xlsx_path: Path,
    *,
    settings: object | None = None,
    strict: bool = False,
) -> list[BankTransactionRecord]:
    warnings.filterwarnings("ignore", message="Workbook contains no default style")
    parser = _detect_parser(xlsx_path, raise_on_open_error=strict, settings=settings)
    if parser is None:
        message = f"No parser recognized format of {xlsx_path.name}"
        if strict:
            raise BankStatementParseError(message)
        logger.warning(message)
        return []

    transactions = parser.load_transactions(xlsx_path, config=_parser_config(settings, parser))
    return transactions or []


def classify_bank_statement(
    xlsx_path: Path,
    file_hash: str,
    *,
    locale: str = "pt-PT",
    settings: object | None = None,
) -> DocumentMetadata | None:
    """Deterministic classification of a bank statement XLSX.

    Returns DocumentMetadata with confidence=1.0 (no LLM needed), or None if
    the file is not a recognized bank statement format.
    """
    parser = _detect_parser(xlsx_path, raise_on_open_error=True, settings=settings)
    if parser is None:
        return None
    data = parser.parse(xlsx_path, config=_parser_config(settings, parser))
    if data is None:
        raise BankStatementParseError(
            f"Parser {parser.FORMAT.value} could not parse {xlsx_path.name}"
        )

    now = datetime.now().strftime("%Y-%m-%d")

    metadata = DocumentMetadata(
        class_confidence=1.0,
        class_reasoning=f"Deterministic classification: {data.bank_format.value} bank statement",
        date_issued=data.period_start,
        document_type="bank-statement",
        issuing_party=data.issuing_party,
        issuing_party_raw=data.issuing_party_raw,
        document_type_raw="bank-statement",
        document_title=data.account_number,
        total_amount=None,
        total_amount_currency=data.currency,
        hash_content=file_hash,
        hash_file=file_hash,
        source_extension=".xlsx",
        locale=locale,
        page_count=None,
        bank_statement=data.to_sidecar_dict(),
        date_created=now,
        date_updated=now,
    )

    logger.debug(
        f"[BANK-CLASSIFY] {xlsx_path.name}: {data.bank_format.value}, "
        f"issuer={data.issuing_party}, date={data.period_start}, "
        f"account={data.account_number}"
    )

    return metadata
