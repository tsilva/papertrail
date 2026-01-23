"""Pydantic models for document metadata."""

import re
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from papertrail.enums import (
    clean_enum_string,
    load_document_types,
    load_issuing_parties,
    create_dynamic_enum,
    get_document_types,
    get_issuing_parties,
)


# Load enum values at module level for initial type annotations
DOCUMENT_TYPES = load_document_types()
ISSUING_PARTIES = load_issuing_parties()

# Create dynamic enums for Pydantic type annotations
DocumentType = create_dynamic_enum('DocumentType', DOCUMENT_TYPES)
IssuingParty = create_dynamic_enum('IssuingParty', ISSUING_PARTIES)


class DocumentMetadataRaw(BaseModel):
    """
    Raw extracted metadata without enum constraints - first phase extraction.

    Used for the initial LLM extraction where we want exact text as it appears
    on the document, before normalization to canonical values.
    """
    issue_date: str = Field(description="Date issued, format: YYYY-MM-DD.")
    document_type: str = Field(description="Type of document (as seen on document).")
    issuing_party: str = Field(description="Issuer name (exactly as it appears on document).")
    service_name: Optional[str] = Field(default=None, description="Product/service name if applicable.")
    total_amount: Optional[float] = Field(default=None, description="Total currency amount.")
    total_amount_currency: Optional[str] = Field(default=None, description="Currency of the total amount.")
    confidence: float = Field(description="Confidence score between 0 and 1.")
    reasoning: str = Field(description="Why this classification was chosen.")


class DocumentMetadata(BaseModel):
    """
    Full document metadata with hashes, timestamps, and validated enum fields.

    Used after normalization when document_type and issuing_party
    have been mapped to canonical values.
    """
    issue_date: str = Field(description="Date issued, format: YYYY-MM-DD.")
    document_type: DocumentType = Field(description="Type of document.")
    issuing_party: IssuingParty = Field(description="Issuer name.")
    service_name: Optional[str] = Field(default=None, description="Product/service name if applicable.")
    total_amount: Optional[float] = Field(default=None, description="Total currency amount.")
    total_amount_currency: Optional[str] = Field(default=None, description="Currency of the total amount.")
    confidence: float = Field(description="Confidence score between 0 and 1.")
    reasoning: str = Field(description="Why this classification was chosen.")

    # Hash and timestamp fields
    content_hash: str = Field(description="Content-based SHA256 hash (first 8 chars).", alias="hash")
    file_hash: Optional[str] = Field(default=None, description="File-based SHA256 hash for quick filtering.", alias="_old_hash")
    create_date: Optional[str] = Field(default=None, description="Date this metadata was created.")
    update_date: Optional[str] = Field(default=None, description="Date this metadata was last updated.")

    # Raw extracted values before normalization
    document_type_raw: Optional[str] = Field(default=None, description="Original document type as extracted.")
    issuing_party_raw: Optional[str] = Field(default=None, description="Original issuing party as extracted.")

    class Config:
        populate_by_name = True

    @field_validator('issue_date', mode='before')
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

    @field_validator('issuing_party', mode='before')
    @classmethod
    def validate_issuing_party(cls, value):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "$UNKNOWN$"
        if isinstance(value, str):
            value = clean_enum_string(value, "IssuingParty")
            if value not in get_issuing_parties():
                return "$UNKNOWN$"
        return value

    @field_validator('document_type', mode='before')
    @classmethod
    def validate_document_type(cls, value):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "$UNKNOWN$"
        if isinstance(value, str):
            value = clean_enum_string(value, "DocumentType")
            if value not in get_document_types():
                return "$UNKNOWN$"
        return value

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
