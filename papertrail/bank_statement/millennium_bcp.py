"""Millennium BCP bank statement parser."""

import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl

from papertrail.bank_statement.models import BankFormat, BankStatementData
from papertrail.logging_utils import get_logger

logger = get_logger("bank_statement")

FORMAT = BankFormat.MILLENNIUM_BCP

# Row layout
_HEADER_ROW = 8
_DATA_START_ROW = 9

# Header detection: ASCII-stripped lowercase substrings to match
_EXPECTED_HEADERS = {"data lancamento", "descricao", "montante"}


def _strip_diacritics(s: str) -> str:
    """Remove diacritics/accents from a string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def can_parse(ws) -> bool:
    """Detect Millennium BCP format by checking column headers in row 8."""
    headers = set()
    for col in range(1, 8):
        val = ws.cell(row=_HEADER_ROW, column=col).value
        if val:
            headers.add(_strip_diacritics(str(val).strip().lower()))
    return _EXPECTED_HEADERS.issubset(headers)


def _parse_date_str(value: str) -> Optional[str]:
    """Parse DD/MM/YYYY or DD-MM-YYYY to YYYY-MM-DD."""
    if not value or not value.strip():
        return None
    s = value.strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse(xlsx_path: Path) -> Optional[BankStatementData]:
    """Parse a Millennium BCP bank statement XLSX."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    if not can_parse(ws):
        wb.close()
        return None

    # Extract metadata from header rows (1-6)
    # Row 2: account info in C3 (e.g., "0000045615660381 - EUR")
    account_raw = str(ws.cell(row=2, column=3).value or "").strip()
    parts = account_raw.split(" - ")
    account_number = parts[0].strip() if parts else ""
    currency = parts[1].strip() if len(parts) > 1 else "EUR"

    # Row 3: start date in C3 (e.g., "01/01/2026")
    period_start = _parse_date_str(str(ws.cell(row=3, column=3).value or ""))

    # Row 4: end date in C3 (e.g., "31/01/2026")
    period_end = _parse_date_str(str(ws.cell(row=4, column=3).value or ""))

    # Count transaction rows (row 9+, non-empty description in C3)
    transaction_count = 0
    for row in ws.iter_rows(min_row=_DATA_START_ROW, max_col=4):
        if row[2].value is not None:  # C3 = description
            transaction_count += 1

    wb.close()

    if not period_start or not period_end:
        logger.warning(f"Could not parse date range from {xlsx_path.name}")
        return None

    logger.debug(
        f"[BANK-PARSE] {xlsx_path.name}: Millennium BCP, "
        f"account={account_number}, {period_start} to {period_end}, "
        f"{transaction_count} transactions"
    )

    return BankStatementData(
        bank_format=BankFormat.MILLENNIUM_BCP,
        account_number=account_number,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        transaction_count=transaction_count,
        issuing_party="millennium-bcp",
        issuing_party_raw="Millennium BCP",
    )
