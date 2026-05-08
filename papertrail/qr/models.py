"""Data models for QR code extraction."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

DEFAULT_QR_COUNTRY_CODE = "PT"
DEFAULT_QR_CURRENCY = "EUR"
DEFAULT_QR_CURRENCY_BY_COUNTRY = {DEFAULT_QR_COUNTRY_CODE: DEFAULT_QR_CURRENCY}
DEFAULT_PORTUGUESE_INVOICE_DOCUMENT_TYPE_CODES = {
    "FT": "invoice",
    "FS": "invoice",
    "FR": "invoice-receipt",
    "NC": "invoice-credit",
    "ND": "invoice-debit",
    "RC": "receipt",
    "RG": "receipt",
}


class QRCodeType(Enum):
    """Type of QR code detected."""
    PORTUGUESE_INVOICE = "portuguese_invoice"
    URL = "url"
    UNKNOWN = "unknown"


@dataclass
class QRCodeData:
    """Raw QR code data extracted from a PDF page."""
    raw_content: str
    qr_type: QRCodeType
    page_number: int
    confidence: float = 1.0


@dataclass
class QRExtractedMetadata:
    """Metadata extracted from a QR code, ready for merging with LLM results."""
    issue_date: Optional[str] = None
    document_type: Optional[str] = None
    total_amount: Optional[float] = None
    total_amount_currency: Optional[str] = DEFAULT_QR_CURRENCY
    issuer_nif: Optional[str] = None
    issuer_tax_number: Optional[str] = None
    atcud: Optional[str] = None
    document_number: Optional[str] = None
    confidence: float = 1.0
    extraction_source: str = "qr"
    locale: Optional[str] = None


@dataclass
class PortugueseInvoiceQR:
    """Parsed Portuguese invoice QR code (Portaria 195/2020 format)."""
    issuer_nif: str
    buyer_nif: Optional[str] = None
    country_code: str = DEFAULT_QR_COUNTRY_CODE
    document_type_code: str = ""
    document_status: str = "N"
    document_date: str = ""
    document_number: str = ""
    atcud: str = ""
    tax_base_exempt: float = 0.0
    tax_base_reduced: float = 0.0
    tax_reduced: float = 0.0
    tax_base_intermediate: float = 0.0
    tax_intermediate: float = 0.0
    tax_base_normal: float = 0.0
    tax_normal: float = 0.0
    tax_base_stamp: float = 0.0
    total_tax: float = 0.0
    gross_total: float = 0.0
    withholding_tax: float = 0.0
    hash_code: str = ""
    certificate_number: str = ""
    extra_fields: dict = field(default_factory=dict)

    def to_extracted_metadata(
        self,
        *,
        currency_by_country: dict[str, str] | None = None,
        default_currency: str = DEFAULT_QR_CURRENCY,
        document_type_codes: dict[str, str] | None = None,
    ) -> QRExtractedMetadata:
        """Convert to QRExtractedMetadata for pipeline integration."""
        issue_date = None
        if self.document_date and len(self.document_date) == 8:
            issue_date = (
                f"{self.document_date[:4]}-"
                f"{self.document_date[4:6]}-"
                f"{self.document_date[6:8]}"
            )

        doc_type_map = document_type_codes or DEFAULT_PORTUGUESE_INVOICE_DOCUMENT_TYPE_CODES
        document_type = doc_type_map.get(self.document_type_code)

        issuer_tax_number = self.issuer_nif if self.issuer_nif else None

        locale = None
        if self.country_code:
            locale = f"{self.country_code.lower()}-{self.country_code}"
        currencies = currency_by_country or {DEFAULT_QR_COUNTRY_CODE: default_currency}
        currency = (
            currencies.get(self.country_code.upper(), default_currency)
            if self.country_code
            else default_currency
        )

        return QRExtractedMetadata(
            issue_date=issue_date,
            document_type=document_type,
            total_amount=self.gross_total if self.gross_total > 0 else None,
            total_amount_currency=currency,
            issuer_nif=self.issuer_nif,
            issuer_tax_number=issuer_tax_number,
            atcud=self.atcud,
            document_number=self.document_number,
            confidence=1.0,
            extraction_source="qr",
            locale=locale,
        )
