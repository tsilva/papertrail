"""Shared helpers for bank statement parser configuration."""

from __future__ import annotations

from typing import Mapping

from papertrail.bank_statement.models import parse_bank_date, parse_bank_date_cell
from papertrail.utils import strip_diacritics

ParserConfig = Mapping[str, object]


def cfg_int(config: ParserConfig | None, key: str, default: int) -> int:
    try:
        return int((config or {}).get(key, default))
    except (TypeError, ValueError):
        return default


def cfg_sequence(
    config: ParserConfig | None,
    key: str,
    default: tuple[str, ...] | set[str],
) -> tuple[str, ...]:
    value = (config or {}).get(key, default)
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in (value or ()))


def cfg_cell(
    config: ParserConfig | None,
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


def cfg_str(config: ParserConfig | None, key: str, default: str) -> str:
    return str((config or {}).get(key, default) or default)


def normalized_headers(ws, *, row: int, columns: int) -> set[str]:
    headers: set[str] = set()
    for col in range(1, columns + 1):
        value = ws.cell(row=row, column=col).value
        if value:
            headers.add(strip_diacritics(str(value).strip().lower()))
    return headers


def expected_headers(
    config: ParserConfig | None,
    default: tuple[str, ...] | set[str],
) -> set[str]:
    return {
        strip_diacritics(header.strip().lower())
        for header in cfg_sequence(config, "expected_headers", default)
    }


def parse_date_str(
    value: str,
    config: ParserConfig | None,
    default_formats: tuple[str, ...],
) -> str | None:
    return parse_bank_date(value, cfg_sequence(config, "date_formats", default_formats))


def parse_date_cell(
    value: object,
    config: ParserConfig | None,
    default_formats: tuple[str, ...],
) -> str | None:
    return parse_bank_date_cell(value, cfg_sequence(config, "date_formats", default_formats))
