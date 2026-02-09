"""Bank statement format detection and classification dispatcher."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl

from papertrail.bank_statement.models import BankFormat, BankStatementData
from papertrail.bank_statement import millennium_bcp
from papertrail.hashing import hash_file_fast
from papertrail.logging_utils import get_logger
from papertrail.models import DocumentMetadata

logger = get_logger("bank_statement")

# Registry of parsers: each must have can_parse(ws) and parse(xlsx_path)
_PARSERS = [millennium_bcp]


def detect_bank_format(xlsx_path: Path) -> Optional[BankFormat]:
    """Detect bank statement format by trying each parser."""
    try:
        wb = openpyxl.load_workbook(xlsx_path, data_only=True)
        ws = wb.active
    except Exception as e:
        logger.debug(f"Could not open {xlsx_path.name}: {e}")
        return None

    try:
        for parser in _PARSERS:
            if parser.can_parse(ws):
                return parser.FORMAT
    finally:
        wb.close()

    return None


def is_bank_statement(xlsx_path: Path) -> bool:
    """Check if an XLSX file is a recognized bank statement."""
    return detect_bank_format(xlsx_path) is not None


def classify_bank_statement(xlsx_path: Path, file_hash: str) -> Optional[DocumentMetadata]:
    """Deterministic classification of a bank statement XLSX.

    Returns DocumentMetadata with confidence=1.0 (no LLM needed), or None if
    the file is not a recognized bank statement format.
    """
    for parser in _PARSERS:
        data = parser.parse(xlsx_path)
        if data is not None:
            break
    else:
        return None

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
        locale="pt-PT",
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
