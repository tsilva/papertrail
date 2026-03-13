"""Pydantic models and metadata validators."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


def clean_enum_string(value: str, enum_prefix: Optional[str] = None) -> str:
    """Remove enum prefixes from serialized strings."""
    if not isinstance(value, str):
        return value
    if enum_prefix:
        prefix = f"{enum_prefix}."
        if value.startswith(prefix):
            return value.split(".", 1)[-1]
    elif "." in value and value.count(".") == 1:
        return value.split(".", 1)[-1]
    return value


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
    def _normalize_blank_required_strings(cls, value):
        if value is None:
            return "$UNKNOWN$"
        if isinstance(value, str):
            cleaned = clean_enum_string(value).strip()
            return cleaned or "$UNKNOWN$"
        return value

    @field_validator("document_type_raw", "issuing_party_raw", mode="before")
    @classmethod
    def _normalize_blank_raw_strings(cls, value):
        if value is None:
            return "$UNKNOWN$"
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or "$UNKNOWN$"
        return value


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
    qrcode: Optional[dict] = Field(default=None)
    bank_statement: Optional[dict] = Field(default=None)
    source_extension: Optional[str] = Field(default=None)
    sub_documents: Optional[list[dict]] = Field(default=None)

    @field_validator("date_issued", mode="before")
    @classmethod
    def validate_issue_date(cls, value):
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
    def normalize_required_strings(cls, value):
        if value is None:
            return "$UNKNOWN$"
        if isinstance(value, str):
            cleaned = clean_enum_string(value).strip()
            return cleaned or "$UNKNOWN$"
        return value

    @field_validator("document_type_raw", "issuing_party_raw", mode="before")
    @classmethod
    def normalize_raw_strings(cls, value):
        if value is None:
            return value
        if isinstance(value, str):
            cleaned = value.strip()
            return cleaned or "$UNKNOWN$"
        return value

    @field_validator("total_amount", mode="before")
    @classmethod
    def clean_and_validate_amount(cls, value):
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
    def normalize_currency(cls, value):
        if value is None:
            return None
        value = value.strip().upper()
        currency_map = {"€": "EUR", "EURO": "EUR", "$": "USD", "£": "GBP"}
        return currency_map.get(value, value)
