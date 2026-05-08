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


def _cfg_int(config: dict[str, object] | None, key: str, default: int) -> int:
    try:
        return int((config or {}).get(key, default))
    except (TypeError, ValueError):
        return default


def _cfg_sequence(
    config: dict[str, object] | None,
    key: str,
    default: tuple[str, ...] | set[str],
) -> tuple[str, ...]:
    value = (config or {}).get(key, default)
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in (value or ()))


def _cfg_cell(
    config: dict[str, object] | None,
    key: str,
    default: tuple[int, int],
) -> tuple[int, int]:
    value = (config or {}).get(key)
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            pass
    return default


def _cfg_str(config: dict[str, object] | None, key: str, default: str) -> str:
    return str((config or {}).get(key, default) or default)


def can_parse(ws, config: dict[str, object] | None = None) -> bool:
    """Detect Millennium BCP format by checking column headers in row 8."""
    header_row = _cfg_int(config, "header_row", _HEADER_ROW)
    scan_columns = _cfg_int(config, "scan_columns", 7)
    expected_headers = {
        strip_diacritics(header.strip().lower())
        for header in _cfg_sequence(config, "expected_headers", _EXPECTED_HEADERS)
    }
    headers = set()
    for col in range(1, scan_columns + 1):
        val = ws.cell(row=header_row, column=col).value
        if val:
            headers.add(strip_diacritics(str(val).strip().lower()))
    return expected_headers.issubset(headers)


_DATE_FORMATS = ("%d/%m/%Y", "%d-%m-%Y")


def _date_formats(config: dict[str, object] | None = None) -> tuple[str, ...]:
    return _cfg_sequence(config, "date_formats", _DATE_FORMATS)


def _parse_date_str(value: str, config: dict[str, object] | None = None) -> str | None:
    return parse_bank_date(value, _date_formats(config))


def parse(xlsx_path: Path, config: dict[str, object] | None = None) -> BankStatementData | None:
    """Parse a Millennium BCP bank statement XLSX."""
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb.active

    if not can_parse(ws, config=config):
        wb.close()
        return None

    account_row, account_col = _cfg_cell(config, "account_cell", (2, 3))
    account_raw = str(ws.cell(row=account_row, column=account_col).value or "").strip()
    parts = account_raw.split(_cfg_str(config, "account_currency_separator", " - "))
    account_number = parts[0].strip() if parts else ""
    currency = parts[1].strip() if len(parts) > 1 else _cfg_str(config, "default_currency", "EUR")

    start_row, start_col = _cfg_cell(config, "period_start_cell", (3, 3))
    end_row, end_col = _cfg_cell(config, "period_end_cell", (4, 3))
    period_start = _parse_date_str(
        str(ws.cell(row=start_row, column=start_col).value or ""),
        config,
    )
    period_end = _parse_date_str(str(ws.cell(row=end_row, column=end_col).value or ""), config)

    transaction_count = 0
    data_start_row = _cfg_int(config, "data_start_row", _DATA_START_ROW)
    max_columns = _cfg_int(config, "max_columns", 4)
    description_column = _cfg_int(config, "description_column", 3) - 1
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
        issuing_party=_cfg_str(config, "issuer_party", "MillenniumBCP"),
        issuing_party_raw=_cfg_str(config, "issuer_party_raw", "Millennium BCP"),
    )


def _parse_date_cell(value, config: dict[str, object] | None = None) -> str | None:
    return parse_bank_date_cell(value, _date_formats(config))


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
    data_start_row = _cfg_int(config, "data_start_row", _DATA_START_ROW)
    max_columns = _cfg_int(config, "max_columns", 7)
    description_column = _cfg_int(config, "description_column", 3) - 1
    amount_column = _cfg_int(config, "amount_column", 4) - 1
    currency_column = _cfg_int(config, "currency_column", 5) - 1
    notes_column = _cfg_int(config, "notes_column", 6) - 1
    treated_column = _cfg_int(config, "treated_column", 7) - 1
    untreated_values = {
        value.lower()
        for value in _cfg_sequence(config, "untreated_values", ("nao", "não", ""))
    }
    default_currency = _cfg_str(config, "default_currency", "EUR")
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
            "date_posting": _parse_date_cell(row[0].value, config),
            "date_value": _parse_date_cell(row[1].value, config),
            "description": str(row[description_column].value or "").strip(),
            "amount": amount,
            "currency": str(row[currency_column].value or default_currency).strip(),
            "notes": str(row[notes_column].value or "").strip(),
            "treated": treated_val,
        })

    wb.close()
    return transactions
