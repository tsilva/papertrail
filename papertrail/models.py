"""Pydantic models and metadata validators."""

from __future__ import annotations

import re
from datetime import datetime
from typing import NotRequired, Optional, TypedDict

from pydantic import BaseModel, Field, field_validator


def clean_enum_string(value: str, enum_prefix: Optional[str] = None) -> str:
    """Remove enum prefixes from serialized strings."""
    if not isinstance(value, str):
        return value
    if enum_prefix:
        prefix = f"{enum_prefix}."
        if value.startswith(prefix):
            return value.split(".", 1)[-1]
    # Only strip implicit enum prefixes for simple identifier-like values.
    elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z0-9_-]+", value):
        return value.split(".", 1)[-1]
    return value


def _normalize_unknown_string(value: object, *, strip_enum_prefix: bool = False) -> object:
    if value is None:
        return "$UNKNOWN$"
    if isinstance(value, str):
        cleaned = clean_enum_string(value).strip() if strip_enum_prefix else value.strip()
        return cleaned or "$UNKNOWN$"
    return value


def _normalize_raw_string(value: object, *, preserve_none: bool) -> object:
    if value is None:
        return None if preserve_none else "$UNKNOWN$"
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or "$UNKNOWN$"
    return value


class QRCodePayload(TypedDict, total=False):
    qr_type: str
    raw_content: str
    page_number: int
    confidence: float


class BankStatementPayload(TypedDict):
    bank_format: str
    account_number: str
    currency: str
    period_start: str
    period_end: str
    transaction_count: int


class SubDocumentPayload(TypedDict, total=False):
    date_issued: str | None
    document_type: str | None
    total_amount: float | None
    total_amount_currency: str | None
    issuer_tax_number: str | None
    issuing_party: str | None
    issuing_party_raw: str | None
    document_number: str | None
    atcud: str | None
    locale: str | None
    qrcode: NotRequired[QRCodePayload | None]


class DocumentMetadataRaw(BaseModel):
    """Single-call extraction: raw text plus normalized forms from the LLM."""

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
    issuer_tax_number: Optional[str] = Field(default=None, description="Issuer tax ID.")
    locale: Optional[str] = Field(default=None, description="Document locale in BCP-47 format.")

    @field_validator("document_type", "issuing_party", mode="before")
    @classmethod
    def _normalize_blank_required_strings(_cls, value):
        return _normalize_unknown_string(value, strip_enum_prefix=True)

    @field_validator("document_type_raw", "issuing_party_raw", mode="before")
    @classmethod
    def _normalize_blank_raw_strings(_cls, value):
        return _normalize_raw_string(value, preserve_none=False)


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
    qrcode: QRCodePayload | None = None


class DocumentMetadata(BaseModel):
    """Full document metadata with hashes and validated field formats."""

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
    qrcode: QRCodePayload | None = Field(default=None)
    bank_statement: BankStatementPayload | None = Field(default=None)
    source_extension: Optional[str] = Field(default=None)
    sub_documents: Optional[list[SubDocumentPayload]] = Field(default=None)

    @field_validator("date_issued", mode="before")
    @classmethod
    def validate_issue_date(_cls, value):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "$UNKNOWN$"
        try:
            parsed_date = datetime.strptime(value, "%Y-%m-%d").date()
            if parsed_date > datetime.now().date():
                raise ValueError(f"issue_date '{value}' is in the future - likely extraction error")
        except ValueError as exc:
            if "future" in str(exc):
                raise
        return value

    @field_validator("document_type", "issuing_party", mode="before")
    @classmethod
    def normalize_required_strings(_cls, value):
        return _normalize_unknown_string(value, strip_enum_prefix=True)

    @field_validator("document_type_raw", "issuing_party_raw", mode="before")
    @classmethod
    def normalize_raw_strings(_cls, value):
        return _normalize_raw_string(value, preserve_none=True)

    @field_validator("total_amount", mode="before")
    @classmethod
    def clean_and_validate_amount(_cls, value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            cleaned = re.sub(r"[^\d,.-]", "", value).replace(".", "").replace(",", ".")
            return float(cleaned)
        raise ValueError(f"Invalid type for amount: {type(value)}")

    @field_validator("total_amount_currency", mode="before")
    @classmethod
    def normalize_currency(_cls, value):
        if value is None:
            return None
        value = value.strip().upper()
        currency_map = {"€": "EUR", "EURO": "EUR", "$": "USD", "£": "GBP"}
        return currency_map.get(value, value)
