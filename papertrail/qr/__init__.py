"""QR extraction models and entry points."""

from papertrail.qr.models import (
    QRCodeType,
    QRCodeData,
    QRExtractedMetadata,
    PortugueseInvoiceQR,
)
from papertrail.qr.extractor import (
    extract_qr_codes_from_page,
    extract_all_qr_codes,
    extract_metadata_from_qr,
    extract_all_metadata_from_qr,
    check_pyzbar_available,
    detect_qr_type,
)

__all__ = [
    # Models
    "QRCodeType",
    "QRCodeData",
    "QRExtractedMetadata",
    "PortugueseInvoiceQR",
    # Extraction
    "extract_qr_codes_from_page",
    "extract_all_qr_codes",
    "extract_metadata_from_qr",
    "extract_all_metadata_from_qr",
    "check_pyzbar_available",
    "detect_qr_type",
]
