"""BPI (Banco BPI) bank statement parser.

Parses XLSX exports from BPI Net Empresas (business banking portal).
Sheet name: "Download de MOVIMENTOS"
Row 7: account & currency (e.g., "4-5970154.000.001 (EUR)")
Row 18: column headers
Row 19+: transaction data
"""

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl

from papertrail.bank_statement.models import BankFormat, BankStatementData
from papertrail.logging_utils import get_logger
from papertrail.text_utils import strip_diacritics

logger = get_logger("bank_statement")

FORMAT = BankFormat.BPI

_HEADER_ROW = 18
_DATA_START_ROW = 19

_EXPECTED_HEADERS = {"data mov.", "descricao do movimento", "valor em eur"}


def can_parse(ws) -> bool:
    """Detect BPI format by checking column headers in row 18."""
    headers = set()
    for col in range(1, 8):
        val = ws.cell(row=_HEADER_ROW, column=col).value
        if val:
            headers.add(strip_diacritics(str(val).strip().lower()))
    return _EXPECTED_HEADERS.issubset(headers)


def _parse_date_str(value: str) -> Optional[str]:
    """Parse DD-MM-YYYY to YYYY-MM-DD."""
    if not value or not value.strip():
        return None
    s = value.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse(xlsx_path: Path) -> Optional[BankStatementData]:
    """Parse a BPI bank statement XLSX."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    if not can_parse(ws):
        wb.close()
        return None

    # Row 7, column 3: account & currency (e.g., "4-5970154.000.001 (EUR)")
    account_raw = str(ws.cell(row=7, column=3).value or "").strip()
    m = re.match(r"([\d\-.]+)\s*\((\w+)\)", account_raw)
    if m:
        account_number = m.group(1).strip()
        currency = m.group(2).strip()
    else:
        account_number = account_raw
        currency = "EUR"

    # Scan transaction rows (19+) for dates and count
    dates = []
    transaction_count = 0
    for row in ws.iter_rows(min_row=_DATA_START_ROW, max_col=4):
        cell_val = row[0].value  # Column A: "Data Mov."
        if cell_val is None:
            continue
        date_str = str(cell_val).strip()
        parsed = _parse_date_str(date_str)
        if parsed:
            dates.append(parsed)
            transaction_count += 1

    wb.close()

    if not dates:
        logger.warning(f"No transaction dates found in {xlsx_path.name}")
        return None

    period_start = min(dates)
    period_end = max(dates)

    logger.debug(
        f"[BANK-PARSE] {xlsx_path.name}: BPI, "
        f"account={account_number}, {period_start} to {period_end}, "
        f"{transaction_count} transactions"
    )

    return BankStatementData(
        bank_format=BankFormat.BPI,
        account_number=account_number,
        currency=currency,
        period_start=period_start,
        period_end=period_end,
        transaction_count=transaction_count,
        issuing_party="bpi",
        issuing_party_raw="BPI",
    )


def _parse_date_cell(value) -> Optional[str]:
    """Parse a date cell value (datetime object or string) to YYYY-MM-DD."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return _parse_date_str(str(value))


def _parse_amount(value) -> Optional[float]:
    """Parse an amount cell value, handling European comma decimal format."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        pass
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None


def load_transactions(xlsx_path: Path) -> Optional[list[dict]]:
    """Load transactions from a BPI bank statement.

    Returns list of transaction dicts, or None if not a recognized format.
    BPI has no "treated" or "notes" columns — all transactions are included.
    """
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    if not can_parse(ws):
        wb.close()
        return None

    transactions = []
    for row in ws.iter_rows(min_row=_DATA_START_ROW, max_col=4):
        if row[0].value is None:  # Data Mov. column A
            continue

        amount = _parse_amount(row[3].value)
        if amount is None:
            continue

        transactions.append({
            "row_number": row[0].row,
            "date_posting": _parse_date_cell(row[0].value),
            "date_value": _parse_date_cell(row[1].value),
            "description": str(row[2].value or "").strip(),
            "amount": amount,
            "currency": "EUR",
            "notes": "",
            "treated": "",
        })

    wb.close()
    return transactions
