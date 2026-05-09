"""Data models for bank statement classification."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol, TypedDict


class BankFormat(Enum):
    """Supported bank statement formats."""
    MILLENNIUM_BCP = "millennium_bcp"
    BPI = "bpi"


@dataclass
class BankStatementData:
    """Extracted bank statement metadata."""
    bank_format: BankFormat
    account_number: str
    currency: str
    period_start: str  # YYYY-MM-DD
    period_end: str  # YYYY-MM-DD
    transaction_count: int
    issuing_party: str  # canonical issuer name (e.g., "MillenniumBCP")
    issuing_party_raw: str  # display name (e.g., "Millennium BCP")

    def to_sidecar_dict(self) -> "BankStatementSidecar":
        """Convert to dict for storage in JSON sidecar `bank_statement` field."""
        return {
            "bank_format": self.bank_format.value,
            "account_number": self.account_number,
            "currency": self.currency,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "transaction_count": self.transaction_count,
        }


class BankStatementSidecar(TypedDict):
    bank_format: str
    account_number: str
    currency: str
    period_start: str
    period_end: str
    transaction_count: int


class BankTransactionRecord(TypedDict):
    row_number: int
    date_posting: str | None
    date_value: str | None
    description: str
    amount: float
    currency: str
    notes: str
    treated: str


class BankStatementParser(Protocol):
    FORMAT: BankFormat

    def can_parse(self, ws, config: Mapping[str, object] | None = None) -> bool: ...
    def parse(
        self,
        xlsx_path: Path,
        config: Mapping[str, object] | None = None,
    ) -> BankStatementData | None: ...
    def load_transactions(
        self,
        xlsx_path: Path,
        config: Mapping[str, object] | None = None,
    ) -> list[BankTransactionRecord] | None: ...


def parse_bank_date(value: str, formats: tuple[str, ...]) -> str | None:
    if not value or not value.strip():
        return None
    stripped = value.strip()
    for fmt in formats:
        try:
            return datetime.strptime(stripped, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def parse_bank_date_cell(value: object, formats: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return parse_bank_date(str(value), formats)


def parse_bank_amount(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()
    if not text:
        return None

    try:
        return float(text)
    except (TypeError, ValueError):
        pass

    normalized = text.replace(" ", "").replace("\u00a0", "")
    if "." in normalized and "," in normalized:
        if normalized.rfind(",") > normalized.rfind("."):
            normalized = normalized.replace(".", "").replace(",", ".")
        else:
            normalized = normalized.replace(",", "")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")

    try:
        return float(normalized)
    except (TypeError, ValueError):
        return None
