"""Core QR code extraction from PDF files."""

import io
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image

from papertrail.logging_utils import get_logger
from papertrail.qr.models import QRCodeData, QRCodeType, QRExtractedMetadata
from papertrail.qr.handlers import detect_qr_type, get_handler_for_content

logger = get_logger('qr.extractor')

# Lazy import pyzbar to allow graceful degradation
_pyzbar_available = None
_pyzbar_decode = None


def _get_pyzbar_decode():
    """Lazy load pyzbar.decode function."""
    global _pyzbar_available, _pyzbar_decode

    if _pyzbar_available is None:
        try:
            from pyzbar.pyzbar import decode
            _pyzbar_decode = decode
            _pyzbar_available = True
            logger.debug("pyzbar loaded successfully")
        except ImportError as e:
            _pyzbar_available = False
            logger.warning(f"pyzbar not available: {e}. QR extraction disabled.")
        except Exception as e:
            _pyzbar_available = False
            logger.warning(f"pyzbar initialization failed: {e}. QR extraction disabled.")

    return _pyzbar_decode if _pyzbar_available else None


def extract_qr_codes_from_page(page: fitz.Page, dpi: int = 300) -> list[QRCodeData]:
    """
    Extract QR codes from a single PDF page.

    Args:
        page: PyMuPDF page object
        dpi: Resolution for rendering (higher = better detection, slower)

    Returns:
        List of QRCodeData objects for each detected QR code
    """
    decode = _get_pyzbar_decode()
    if decode is None:
        return []

    qr_codes = []

    # Render page at specified DPI
    zoom = dpi / 72  # PDF default is 72 DPI
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat)

    # Convert to PIL Image for pyzbar
    img = Image.open(io.BytesIO(pix.tobytes("png")))

    # Decode QR codes
    try:
        decoded_objects = decode(img)
    except Exception as e:
        logger.debug(f"pyzbar decode failed: {e}")
        return []

    for obj in decoded_objects:
        # pyzbar returns bytes, decode to string
        try:
            raw_content = obj.data.decode("utf-8")
        except UnicodeDecodeError:
            # Try latin-1 as fallback
            try:
                raw_content = obj.data.decode("latin-1")
            except Exception:
                logger.debug(f"Could not decode QR content: {obj.data[:50]}...")
                continue

        qr_type = detect_qr_type(raw_content)

        qr_codes.append(QRCodeData(
            raw_content=raw_content,
            qr_type=qr_type,
            page_number=page.number,
            confidence=1.0,
        ))

    return qr_codes


def extract_all_qr_codes(pdf_path: Path, max_pages: int = 5, include_last: bool = True) -> list[QRCodeData]:
    """
    Extract QR codes from a PDF file.

    Scans the first few pages and optionally the last page (common locations for QR codes).

    Args:
        pdf_path: Path to the PDF file
        max_pages: Maximum number of initial pages to scan
        include_last: Also scan the last page if not already included

    Returns:
        List of QRCodeData objects
    """
    if _get_pyzbar_decode() is None:
        return []

    all_qr_codes = []
    pages_scanned = set()

    try:
        with fitz.open(str(pdf_path)) as doc:
            total_pages = len(doc)

            # Scan first N pages
            for i in range(min(max_pages, total_pages)):
                qr_codes = extract_qr_codes_from_page(doc[i])
                all_qr_codes.extend(qr_codes)
                pages_scanned.add(i)

            # Scan last page if not already included
            if include_last and (total_pages - 1) not in pages_scanned:
                qr_codes = extract_qr_codes_from_page(doc[total_pages - 1])
                all_qr_codes.extend(qr_codes)

    except Exception as e:
        logger.warning(f"QR extraction failed for {pdf_path.name}: {e}")
        return []

    if all_qr_codes:
        logger.debug(f"Found {len(all_qr_codes)} QR code(s) in {pdf_path.name}")
        for qr in all_qr_codes:
            logger.debug(f"  Page {qr.page_number}: {qr.qr_type.value} - {qr.raw_content[:50]}...")

    return all_qr_codes


def extract_metadata_from_qr(pdf_path: Path) -> Optional[QRExtractedMetadata]:
    """
    Main entry point: Extract metadata from QR codes in a PDF.

    Scans the PDF for QR codes, parses them using the appropriate handler,
    and returns extracted metadata.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        QRExtractedMetadata if a useful QR code was found and parsed, None otherwise
    """
    qr_codes = extract_all_qr_codes(pdf_path)

    if not qr_codes:
        return None

    # Try to extract metadata from each QR code (prioritize structured formats over URLs)
    # Sort by priority: Portuguese invoice > other structured > URL > unknown
    priority_order = {
        QRCodeType.PORTUGUESE_INVOICE: 0,
        QRCodeType.URL: 10,
        QRCodeType.UNKNOWN: 20,
    }
    qr_codes.sort(key=lambda x: priority_order.get(x.qr_type, 15))

    for qr_data in qr_codes:
        handler = get_handler_for_content(qr_data.raw_content)
        if handler:
            metadata = handler.parse(qr_data)
            if metadata:
                logger.debug(f"Extracted metadata from {qr_data.qr_type.value} QR: "
                           f"date={metadata.issue_date}, type={metadata.document_type}, "
                           f"amount={metadata.total_amount}")
                return metadata

    return None
