"""Pydantic models for document metadata."""

import re
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from papertrail.enums import (
    clean_enum_string,
    get_document_types,
)
from papertrail.logging_utils import get_logger

logger = get_logger('models')


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
        # New value — pass through (will be confirmed interactively downstream)
        return value
    return value


class DocumentMetadataRaw(BaseModel):
    """Phase 1 raw extraction: exact text as it appears on the document."""
    issue_date: str = Field(description="Date issued, format: YYYY-MM-DD.")
    document_type: str = Field(description="Core document type label only, stripped of dates/periods/numbers (e.g., 'Fatura' not 'Fatura de Agosto 2021').")
    document_title: Optional[str] = Field(default=None, description="Specific subject, product, service, or transaction described in the document. Null if no specific subject beyond the document type is identifiable.")
    issuing_party: str = Field(description="Issuer name (exactly as it appears on document).")
    total_amount: Optional[float] = Field(default=None, description="Total currency amount.")
    total_amount_currency: Optional[str] = Field(default=None, description="Currency of the total amount.")
    confidence: float = Field(description="Confidence score between 0 and 1.")
    reasoning: str = Field(description="Why this classification was chosen.")
    issuer_tax_number: Optional[str] = Field(
        default=None,
        description="Issuer's tax identification number (VAT, NIF, EIN). Include country prefix when visible (e.g., DETESTOWNER, IE1234567X)."
    )
    locale: Optional[str] = Field(
        default=None,
        description="Document locale in BCP-47 format (e.g., 'pt-PT'). Detect from language, currency, date format, tax ID format."
    )


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
    class_confidence: float = Field(description="Confidence score between 0 and 1.")
    class_reasoning: str = Field(description="Why this classification was chosen.")
    date_created: Optional[str] = Field(default=None)
    date_issued: str = Field(description="Date issued, format: YYYY-MM-DD.")
    date_updated: Optional[str] = Field(default=None)
    document_type: str = Field(description="Type of document.")
    issuing_party: str = Field(description="Issuer name.")
    total_amount: Optional[float] = Field(default=None)
    total_amount_currency: Optional[str] = Field(default=None)
    hash_content: str = Field(description="Content-based hash (first 8 hex chars).")
    hash_file: Optional[str] = Field(default=None)
    hash_text: Optional[str] = Field(default=None)
    document_type_raw: Optional[str] = Field(default=None)
    document_title: Optional[str] = Field(default=None, description="Specific subject/product/service described in the document.")
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


def normalize_enum_field_in_dict(data: dict, field_name: str, enum_prefix: str) -> None:
    """Normalize enum fields in metadata dict (mutates in place)."""
    value = data.get(field_name)
    if isinstance(value, Enum):
        data[field_name] = value.value
    elif isinstance(value, str):
        data[field_name] = clean_enum_string(value, enum_prefix)
