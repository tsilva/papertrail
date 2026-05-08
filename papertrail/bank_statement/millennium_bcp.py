"""Millennium BCP bank statement parser."""

from pathlib import Path

import openpyxl

from papertrail.bank_statement.models import (
    BankFormat,
    BankStatementData,
    BankTransactionRecord,
    parse_bank_amount,
    parse_bank_date,
    parse_bank_date_cell,
)
from papertrail.logging_utils import get_logger
from papertrail.utils import strip_diacritics

logger = get_logger("bank_statement")

FORMAT = BankFormat.MILLENNIUM_BCP

_HEADER_ROW = 8
_DATA_START_ROW = 9
_EXPECTED_HEADERS = {"data lancamento", "descricao", "montante"}


def can_parse(ws) -> bool:
    """Detect Millennium BCP format by checking column headers in row 8."""
    headers = set()
    for col in range(1, 8):
        val = ws.cell(row=_HEADER_ROW, column=col).value
        if val:
            headers.add(strip_diacritics(str(val).strip().lower()))
    return _EXPECTED_HEADERS.issubset(headers)


_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y")


def _parse_date_str(value: str) -> str | None:
    return parse_bank_date(value, _DATE_FORMATS)


def parse(xlsx_path: Path) -> BankStatementData | None:
    """Parse a Millennium BCP bank statement XLSX."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    if not can_parse(ws):
        wb.close()
        return None

    account_raw = str(ws.cell(row=2, column=3).value or "").strip()
    parts = account_raw.split(" - ")
    account_number = parts[0].strip() if parts else ""
    currency = parts[1].strip() if len(parts) > 1 else "EUR"

    period_start = _parse_date_str(str(ws.cell(row=3, column=3).value or ""))
    period_end = _parse_date_str(str(ws.cell(row=4, column=3).value or ""))

    transaction_count = 0
    for row in ws.iter_rows(min_row=_DATA_START_ROW, max_col=4):
        if row[2].value is not None:
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
        issuing_party="MillenniumBCP",
        issuing_party_raw="Millennium BCP",
    )


def _parse_date_cell(value) -> str | None:
    return parse_bank_date_cell(value, _DATE_FORMATS)


def load_transactions(xlsx_path: Path) -> list[BankTransactionRecord] | None:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    if not can_parse(ws):
        wb.close()
        return None

    transactions = []
    for row in ws.iter_rows(min_row=_DATA_START_ROW, max_col=7):
        if row[2].value is None:
            continue

        treated_val = str(row[6].value or "").strip()
        if treated_val.lower() not in ("nao", "não", ""):
            continue

        amount = parse_bank_amount(row[3].value)
        if amount is None:
            continue

        transactions.append({
            "row_number": row[0].row,
            "date_posting": _parse_date_cell(row[0].value),
            "date_value": _parse_date_cell(row[1].value),
            "description": str(row[2].value or "").strip(),
            "amount": amount,
            "currency": str(row[4].value or "EUR").strip(),
            "notes": str(row[5].value or "").strip(),
            "treated": treated_val,
        })

    wb.close()
    return transactions
