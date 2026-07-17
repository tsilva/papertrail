"""Millennium BCP bank statement parser."""

from pathlib import Path

import openpyxl

from papertrail.bank_statement.models import (
    BankFormat,
    BankStatementData,
    BankTransactionRecord,
    parse_bank_amount,
)
from papertrail.bank_statement.parser_utils import (
    cfg_cell,
    cfg_int,
    cfg_sequence,
    cfg_str,
    expected_headers,
    normalized_headers,
    parse_date_cell,
    parse_date_str,
)
from papertrail.logging_utils import get_logger

logger = get_logger("bank_statement")

FORMAT = BankFormat.MILLENNIUM_BCP

_HEADER_ROW = 8
_DATA_START_ROW = 9
_EXPECTED_HEADERS = {"data lancamento", "descricao", "montante"}


def can_parse(ws, config: dict[str, object] | None = None) -> bool:
    """Detect Millennium BCP format by checking column headers in row 8."""
    header_row = cfg_int(config, "header_row", _HEADER_ROW)
    scan_columns = cfg_int(config, "scan_columns", 7)
    return expected_headers(config, _EXPECTED_HEADERS).issubset(
        normalized_headers(ws, row=header_row, columns=scan_columns)
    )


_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y")


def parse(xlsx_path: Path, config: dict[str, object] | None = None) -> BankStatementData | None:
    """Parse a Millennium BCP bank statement XLSX."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    if not can_parse(ws, config=config):
        wb.close()
        return None

    account_row, account_col = cfg_cell(config, "account_cell", (2, 3))
    account_raw = str(ws.cell(row=account_row, column=account_col).value or "").strip()
    parts = account_raw.split(cfg_str(config, "account_currency_separator", " - "))
    account_number = parts[0].strip() if parts else ""
    currency = parts[1].strip() if len(parts) > 1 else cfg_str(config, "default_currency", "EUR")

    start_row, start_col = cfg_cell(config, "period_start_cell", (3, 3))
    end_row, end_col = cfg_cell(config, "period_end_cell", (4, 3))
    period_start = parse_date_str(
        str(ws.cell(row=start_row, column=start_col).value or ""),
        config,
        _DATE_FORMATS,
    )
    period_end = parse_date_str(
        str(ws.cell(row=end_row, column=end_col).value or ""),
        config,
        _DATE_FORMATS,
    )

    transaction_count = 0
    data_start_row = cfg_int(config, "data_start_row", _DATA_START_ROW)
    max_columns = cfg_int(config, "max_columns", 4)
    description_column = cfg_int(config, "description_column", 3) - 1
    for row in ws.iter_rows(min_row=data_start_row, max_col=max_columns):
        if row[description_column].value is not None:
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
        issuing_party=cfg_str(config, "issuer_party", "MillenniumBCP"),
        issuing_party_raw=cfg_str(config, "issuer_party_raw", "Millennium BCP"),
    )


def load_transactions(
    xlsx_path: Path,
    config: dict[str, object] | None = None,
) -> list[BankTransactionRecord] | None:
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    if not can_parse(ws, config=config):
        wb.close()
        return None

    transactions = []
    data_start_row = cfg_int(config, "data_start_row", _DATA_START_ROW)
    max_columns = cfg_int(config, "max_columns", 7)
    description_column = cfg_int(config, "description_column", 3) - 1
    amount_column = cfg_int(config, "amount_column", 4) - 1
    currency_column = cfg_int(config, "currency_column", 5) - 1
    notes_column = cfg_int(config, "notes_column", 6) - 1
    treated_column = cfg_int(config, "treated_column", 7) - 1
    untreated_values = {
        value.lower()
        for value in cfg_sequence(config, "untreated_values", ("nao", "não", ""))
    }
    default_currency = cfg_str(config, "default_currency", "EUR")
    for row in ws.iter_rows(min_row=data_start_row, max_col=max_columns):
        if row[description_column].value is None:
            continue

        treated_val = str(row[treated_column].value or "").strip()
        if treated_val.lower() not in untreated_values:
            continue

        amount = parse_bank_amount(row[amount_column].value)
        if amount is None:
            continue

        transactions.append({
            "row_number": row[0].row,
            "date_posting": parse_date_cell(row[0].value, config, _DATE_FORMATS),
            "date_value": parse_date_cell(row[1].value, config, _DATE_FORMATS),
            "description": str(row[description_column].value or "").strip(),
            "amount": amount,
            "currency": str(row[currency_column].value or default_currency).strip(),
            "notes": str(row[notes_column].value or "").strip(),
            "treated": treated_val,
        })

    wb.close()
    return transactions
