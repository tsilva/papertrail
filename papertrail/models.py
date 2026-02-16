"""Pydantic models and dynamic enum loading for document metadata."""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from papertrail.logging_utils import get_logger

logger = get_logger('models')


# --- Dynamic enum loading ---

_DOCUMENT_TYPES_LIST: list[str] | None = None
_ISSUING_PARTIES_LIST: list[str] | None = None
_session_types: set[str] = set()
_session_parties: set[str] = set()


def reset_enum_cache() -> None:
    """Reset the enum cache, forcing re-evaluation on next access."""
    global _DOCUMENT_TYPES_LIST, _ISSUING_PARTIES_LIST
    _DOCUMENT_TYPES_LIST = None
    _ISSUING_PARTIES_LIST = None


def reset_session_cache() -> None:
    """Reset session-scoped confirmed values."""
    _session_types.clear()
    _session_parties.clear()


def add_session_type(value: str) -> None:
    _session_types.add(value)


def add_session_party(value: str) -> None:
    _session_parties.add(value)


def clean_enum_string(value: str, enum_prefix: Optional[str] = None) -> str:
    """Remove enum prefix from serialized strings. E.g. 'DocumentType.invoice' -> 'invoice'."""
    if not isinstance(value, str):
        return value
    if enum_prefix:
        prefix = f"{enum_prefix}."
        if value.startswith(prefix):
            return value.split(".", 1)[-1]
    elif "." in value and value.count(".") == 1:
        return value.split(".", 1)[-1]
    return value


def _scan_json_field(processed_dir: str, field: str) -> set[str]:
    """Scan processed JSON files and collect unique values for a field."""
    values = set()
    path = Path(processed_dir)
    if not path.exists():
        return values
    for json_file in path.rglob("*.json"):
        if json_file.name.endswith(".reconciliation.json"):
            continue
        if any(part.startswith("_dupes") for part in json_file.parts):
            continue
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                value = json.load(f).get(field)
            if value and isinstance(value, str) and value != "$UNKNOWN$":
                values.add(clean_enum_string(value, "DocumentType" if field == "document_type" else None))
        except Exception:
            continue
    return values


def _load_values(field: str, session_set: set[str]) -> list[str]:
    """Load values from processed files + session confirmations."""
    from papertrail.config import get_current_profile
    profile = get_current_profile()
    processed_dir = profile.paths.processed if profile else None
    values_set = _scan_json_field(processed_dir, field) if processed_dir else set()
    values_set |= session_set
    values_set.add("$UNKNOWN$")
    return sorted(values_set)


def _get_cached(cached: list[str] | None, field: str, session_set: set[str]) -> list[str]:
    """Return cached list with session-aware invalidation."""
    if cached is None or session_set - set(cached):
        cached = _load_values(field, session_set)
    return cached


def get_document_types() -> list[str]:
    global _DOCUMENT_TYPES_LIST
    _DOCUMENT_TYPES_LIST = _get_cached(_DOCUMENT_TYPES_LIST, "document_type", _session_types)
    return _DOCUMENT_TYPES_LIST


def get_issuing_parties() -> list[str]:
    global _ISSUING_PARTIES_LIST
    _ISSUING_PARTIES_LIST = _get_cached(_ISSUING_PARTIES_LIST, "issuing_party", _session_parties)
    return _ISSUING_PARTIES_LIST


# --- Pydantic models ---

def _normalize_to_known(value, enum_name: str, getter, field_label: str):
    """Normalize a value to its known canonical form, or pass through as-is if new."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return "$UNKNOWN$"
    if isinstance(value, str):
        value = clean_enum_string(value, enum_name)
        valid = getter()
        valid_lower = {v.lower(): v for v in valid}
        value_lower = value.lower()
        if value_lower in valid_lower:
            return valid_lower[value_lower]
        return value
    return value


class DocumentMetadataRaw(BaseModel):
    """Single-call extraction: raw text + normalized canonical forms."""
    issue_date: str = Field(description="Date issued, format: YYYY-MM-DD.")
    document_type: str = Field(description="Normalized document type slug.")
    document_type_raw: str = Field(description="Core document type label as on document.")
    document_title: Optional[str] = Field(default=None, description="Specific subject/product/service.")
    issuing_party: str = Field(description="Normalized issuer name slug.")
    issuing_party_raw: str = Field(default="$UNKNOWN$", description="Issuer name as on document.")
    total_amount: Optional[float] = Field(default=None, description="Total currency amount.")
    total_amount_currency: Optional[str] = Field(default=None, description="Currency of amount.")
    confidence: float = Field(default=0.0, description="Confidence score 0-1.")
    reasoning: str = Field(default="", description="Why this classification was chosen.")
    issuer_tax_number: Optional[str] = Field(default=None, description="Issuer's tax ID.")
    locale: Optional[str] = Field(default=None, description="Document locale in BCP-47 format.")


class SubDocumentMetadata(BaseModel):
    """QR-extracted metadata for a single sub-document within a multi-invoice PDF."""
    date_issued: Optional[str] = None
    document_type: Optional[str] = None
    total_amount: Optional[float] = None
    total_amount_currency: Optional[str] = None
    issuer_tax_number: Optional[str] = None
    issuing_party: Optional[str] = None
    issuing_party_raw: Optional[str] = None
    document_number: Optional[str] = None
    atcud: Optional[str] = None
    locale: Optional[str] = None
    qrcode: Optional[dict] = None


class DocumentMetadata(BaseModel):
    """Full document metadata with hashes, timestamps, and validated enum fields."""
    class_confidence: float = Field(description="Confidence score 0-1.")
    class_reasoning: str = Field(description="Why this classification was chosen.")
    date_created: Optional[str] = Field(default=None)
    date_issued: str = Field(description="Date issued, format: YYYY-MM-DD.")
    date_updated: Optional[str] = Field(default=None)
    document_type: str = Field(description="Type of document.")
    issuing_party: str = Field(description="Issuer name.")
    total_amount: Optional[float] = Field(default=None)
    total_amount_currency: Optional[str] = Field(default=None)
    hash_content: str = Field(description="Content-based hash (8 hex chars).")
    hash_file: Optional[str] = Field(default=None)
    hash_text: Optional[str] = Field(default=None)
    document_type_raw: Optional[str] = Field(default=None)
    document_title: Optional[str] = Field(default=None)
    issuing_party_raw: Optional[str] = Field(default=None)
    page_count: Optional[int] = Field(default=None)
    file_size_kb: Optional[int] = Field(default=None)
    issuer_tax_number: Optional[str] = Field(default=None)
    locale: Optional[str] = Field(default=None)
    qrcode: Optional[dict] = Field(default=None)
    bank_statement: Optional[dict] = Field(default=None)
    source_extension: Optional[str] = Field(default=None)
    sub_documents: Optional[list[dict]] = Field(default=None)

    @field_validator('date_issued', mode='before')
    @classmethod
    def validate_issue_date(cls, value):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "$UNKNOWN$"
        try:
            parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
            if parsed_date > datetime.now().date():
                raise ValueError(f"issue_date '{value}' is in the future - likely extraction error")
        except ValueError as e:
            if "future" in str(e):
                raise
        return value

    @field_validator('document_type', mode='before')
    @classmethod
    def validate_document_type(cls, value):
        return _normalize_to_known(value, "DocumentType", get_document_types, "document_type")

    @field_validator('total_amount', mode='before')
    @classmethod
    def clean_and_validate_amount(cls, value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            value = re.sub(r'[^\d,.-]', '', value).replace('.', '').replace(',', '.')
            return float(value)
        raise ValueError(f"Invalid type for amount: {type(value)}")

    @field_validator('total_amount_currency', mode='before')
    @classmethod
    def normalize_currency(cls, value):
        if value is None:
            return None
        value = value.strip().upper()
        currency_map = {'€': 'EUR', 'EURO': 'EUR', '$': 'USD', '£': 'GBP'}
        return currency_map.get(value, value)
