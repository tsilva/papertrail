"""Data models for QR code extraction."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


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
    total_amount_currency: Optional[str] = "EUR"
    issuer_nif: Optional[str] = None
    atcud: Optional[str] = None
    document_number: Optional[str] = None
    confidence: float = 1.0
    extraction_source: str = "qr"


@dataclass
class PortugueseInvoiceQR:
    """
    Parsed Portuguese invoice QR code (Portaria 195/2020 format).

    Format example:
    A:503782467*B:516158562*C:PT*D:FT*E:N*F:20260126*G:FT 1.1/737*H:JF7SR27M-737*I1:0*I6:232.64*I7:53.51*N:53.51*O:286.15*Q:XXXX*R:0326*

    Key fields:
    - A: Issuer NIF (tax ID)
    - B: Buyer NIF (tax ID)
    - C: Country code (PT)
    - D: Document type (FT=invoice, NC=credit note, FR=receipt, etc.)
    - E: Document status (N=normal, A=canceled)
    - F: Document date (YYYYMMDD)
    - G: Document number/identifier
    - H: ATCUD (unique document code)
    - I1-I8: Tax base amounts by rate
    - J1-J8: Space for autonomous regions
    - K1-K8: Space for autonomous regions
    - N: Total tax amount
    - O: Gross total (including tax)
    - Q: Hash (4 chars)
    - R: Certificate number
    """
    issuer_nif: str  # A
    buyer_nif: Optional[str] = None  # B
    country_code: str = "PT"  # C
    document_type_code: str = ""  # D (FT, NC, FR, etc.)
    document_status: str = "N"  # E (N=normal, A=canceled)
    document_date: str = ""  # F (YYYYMMDD)
    document_number: str = ""  # G
    atcud: str = ""  # H
    tax_base_exempt: float = 0.0  # I1
    tax_base_reduced: float = 0.0  # I2
    tax_reduced: float = 0.0  # I3
    tax_base_intermediate: float = 0.0  # I4
    tax_intermediate: float = 0.0  # I5
    tax_base_normal: float = 0.0  # I6
    tax_normal: float = 0.0  # I7
    tax_base_stamp: float = 0.0  # I8
    total_tax: float = 0.0  # N
    gross_total: float = 0.0  # O
    withholding_tax: float = 0.0  # P
    hash_code: str = ""  # Q (4 chars)
    certificate_number: str = ""  # R

    # Additional parsed fields stored in a dict for flexibility
    extra_fields: dict = field(default_factory=dict)

    def to_extracted_metadata(self) -> QRExtractedMetadata:
        """Convert to QRExtractedMetadata for pipeline integration."""
        # Convert YYYYMMDD to YYYY-MM-DD
        issue_date = None
        if self.document_date and len(self.document_date) == 8:
            issue_date = f"{self.document_date[:4]}-{self.document_date[4:6]}-{self.document_date[6:8]}"

        # Map document type codes to canonical types
        doc_type_map = {
            "FT": "invoice",
            "FS": "invoice",  # Simplified invoice
            "FR": "receipt",
            "NC": "credit-note",
            "ND": "debit-note",
            "RC": "receipt",
            "RG": "receipt",
        }
        document_type = doc_type_map.get(self.document_type_code)

        return QRExtractedMetadata(
            issue_date=issue_date,
            document_type=document_type,
            total_amount=self.gross_total if self.gross_total > 0 else None,
            total_amount_currency="EUR",  # Portuguese invoices are always EUR
            issuer_nif=self.issuer_nif,
            atcud=self.atcud,
            document_number=self.document_number,
            confidence=1.0,
            extraction_source="qr",
        )
