"""Data models for bank statement classification."""

from dataclasses import dataclass
from enum import Enum
from typing import Optional


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
    issuing_party: str  # canonical slug (e.g., "millennium-bcp")
    issuing_party_raw: str  # display name (e.g., "Millennium BCP")

    def to_sidecar_dict(self) -> dict:
        """Convert to dict for storage in JSON sidecar `bank_statement` field."""
        return {
            "bank_format": self.bank_format.value,
            "account_number": self.account_number,
            "currency": self.currency,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "transaction_count": self.transaction_count,
        }
