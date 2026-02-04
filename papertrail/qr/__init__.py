"""
QR code extraction package for papertrail.

This package provides modular QR code extraction support, starting with
Portuguese invoice QR codes (Portaria 195/2020).

Usage:
    from papertrail.qr import extract_metadata_from_qr

    metadata, raw_data = extract_metadata_from_qr(pdf_path)
    if metadata:
        print(f"Date: {metadata.issue_date}, Amount: {metadata.total_amount}")
        # raw_data contains: {"qr_type": "...", "raw_content": "...", "page_number": 0}

Dependencies:
    - pyzbar>=0.1.9 (Python package)
    - zbar (system library: brew install zbar / apt install libzbar0)
"""

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
    check_pyzbar_available,
)
from papertrail.qr.handlers import (
    QRHandler,
    PortugueseInvoiceHandler,
    detect_qr_type,
    get_handler,
    get_handler_for_content,
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
    "check_pyzbar_available",
    # Handlers
    "QRHandler",
    "PortugueseInvoiceHandler",
    "detect_qr_type",
    "get_handler",
    "get_handler_for_content",
]
