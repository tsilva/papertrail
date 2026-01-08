"""Pydantic models for document metadata."""

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

from documentor.enums import (
    clean_enum_string,
    load_document_types,
    load_issuing_parties,
    create_dynamic_enum,
)


def _is_empty_value(value) -> bool:
    """Check if value is None or empty string."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


# Load enum values at module level
DOCUMENT_TYPES = load_document_types()
ISSUING_PARTIES = load_issuing_parties()

# Create dynamic enums
DocumentType = create_dynamic_enum('DocumentType', DOCUMENT_TYPES)
IssuingParty = create_dynamic_enum('IssuingParty', ISSUING_PARTIES)


class DocumentMetadataRaw(BaseModel):
    """
    Raw extracted metadata without enum constraints - first phase extraction.

    Used for the initial LLM extraction where we want exact text as it appears
    on the document, before normalization to canonical values.
    """
    issue_date: str = Field(
        description="Date issued, format: YYYY-MM-DD.",
        examples=["2025-01-02"]
    )
    document_type: str = Field(
        description="Type of document (as seen on document).",
        examples=["Invoice"]
    )
    issuing_party: str = Field(
        description="Issuer name (exactly as it appears on document).",
        examples=["Anthropic, PBC"]
    )
    service_name: Optional[str] = Field(
        default=None,
        description="Product/service name if applicable (as short as possible).",
        examples=["Youtube Premium"]
    )
    total_amount: Optional[float] = Field(
        default=None,
        description="Total currency amount."
    )
    total_amount_currency: Optional[str] = Field(
        default=None,
        description="Currency of the total amount.",
        examples=["EUR"]
    )
    confidence: float = Field(
        description="Confidence score between 0 and 1."
    )
    reasoning: str = Field(
        description="Why this classification was chosen."
    )


class DocumentMetadataInput(BaseModel):
    """
    Document metadata with enum validation.

    Used after normalization when document_type and issuing_party
    have been mapped to canonical values.
    """
    issue_date: str = Field(
        description="Date issued, format: YYYY-MM-DD.",
        examples=["2025-01-02"]
    )
    document_type: DocumentType = Field(
        description="Type of document.",
        examples=["invoice"]
    )
    issuing_party: IssuingParty = Field(
        description="Issuer name, must be one of the predefined issuing parties.",
        examples=["Amazon"]
    )
    service_name: Optional[str] = Field(
        default=None,
        description="Product/service name if applicable (as short as possible).",
        examples=["Youtube Premium"]
    )
    total_amount: Optional[float] = Field(
        default=None,
        description="Total currency amount."
    )
    total_amount_currency: Optional[str] = Field(
        default=None,
        description="Currency of the total amount.",
        examples=["EUR"]
    )
    confidence: float = Field(
        description="Confidence score between 0 and 1."
    )
    reasoning: str = Field(
        description="Why this classification was chosen."
    )


class DocumentMetadata(DocumentMetadataInput):
    """
    Full document metadata with hashes and timestamps.

    Extends DocumentMetadataInput with content hash, file hash,
    creation/update dates, and raw extracted values.
    """
    content_hash: str = Field(
        description="Content-based SHA256 hash (first 8 chars) - based on rendered PDF pages.",
        examples=["a1b2c3d4"],
        alias="hash"
    )
    file_hash: Optional[str] = Field(
        default=None,
        description="File-based SHA256 hash for quick filtering (first 8 chars).",
        examples=["b2c3d4e5"],
        alias="_old_hash"
    )
    create_date: Optional[str] = Field(
        default=None,
        description="Date this metadata was created, format: YYYY-MM-DD.",
        examples=["2024-06-01"]
    )
    update_date: Optional[str] = Field(
        default=None,
        description="Date this metadata was last updated, format: YYYY-MM-DD.",
        examples=["2024-06-01"]
    )
    # Raw extracted values before normalization
    document_type_raw: Optional[str] = Field(
        default=None,
        description="Original document type as extracted from document."
    )
    issuing_party_raw: Optional[str] = Field(
        default=None,
        description="Original issuing party name as extracted from document."
    )

    class Config:
        populate_by_name = True  # Allow both field name and alias

    @field_validator('issue_date', mode='before')
    @classmethod
    def validate_issue_date(cls, value):
        if _is_empty_value(value):
            return "$UNKNOWN$"
        return value

    @field_validator('issuing_party', mode='before')
    @classmethod
    def validate_issuing_party(cls, value):
        if _is_empty_value(value):
            return "$UNKNOWN$"
        if isinstance(value, str):
            value = clean_enum_string(value, "IssuingParty")
            if value not in ISSUING_PARTIES:
                return "$UNKNOWN$"
        return value

    @field_validator('document_type', mode='before')
    @classmethod
    def validate_document_type(cls, value):
        if _is_empty_value(value):
            return "$UNKNOWN$"
        if isinstance(value, str):
            value = clean_enum_string(value, "DocumentType")
            if value not in DOCUMENT_TYPES:
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
            # Remove currency symbols and normalize decimal separators
            value = re.sub(r'[^\d,.-]', '', value).replace('.', '').replace(',', '.')
            return float(value)
        raise ValueError(f"Invalid type for amount: {type(value)}")

    @field_validator('total_amount_currency', mode='before')
    @classmethod
    def normalize_currency(cls, value):
        if value is None:
            return None
        value = value.strip().upper()
        # Map common currency symbols to ISO codes
        currency_map = {
            '€': 'EUR', 'EURO': 'EUR',
            '$': 'USD',
            '£': 'GBP'
        }
        return currency_map.get(value, value)


def normalize_enum_field_in_dict(data: dict, field_name: str, enum_prefix: str) -> None:
    """
    Normalize enum fields in metadata dict (mutates in place).

    Handles both Enum instances and string representations like "DocumentType.invoice".

    Args:
        data: Dictionary to modify
        field_name: Name of the field to normalize
        enum_prefix: The enum prefix to strip (e.g., "DocumentType", "IssuingParty")
    """
    value = data.get(field_name)
    if isinstance(value, Enum):
        data[field_name] = value.value
    elif isinstance(value, str):
        data[field_name] = clean_enum_string(value, enum_prefix)
