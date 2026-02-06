"""Core QR code extraction from PDF files."""

import io
import os
import sys
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF
from PIL import Image

from papertrail.logging_utils import get_logger
from papertrail.qr.models import QRCodeData, QRCodeType, QRExtractedMetadata, PortugueseInvoiceQR

logger = get_logger('qr.extractor')


def is_portuguese_invoice_qr(raw_content: str) -> bool:
    """Check if content matches Portuguese invoice QR format (Portaria 195/2020).

    Detection criteria:
    - Starts with 'A:' (issuer NIF field)
    - Contains '*' delimiters
    - Contains 'H:' (ATCUD field, mandatory since 2022)
    """
    if not raw_content:
        return False
    content = raw_content.strip()
    return content.startswith("A:") and "*" in content and ("*H:" in content or content.startswith("H:"))


def detect_qr_type(raw_content: str) -> QRCodeType:
    """Detect the type of QR code from its content."""
    if not raw_content:
        return QRCodeType.UNKNOWN

    if is_portuguese_invoice_qr(raw_content):
        return QRCodeType.PORTUGUESE_INVOICE

    content = raw_content.strip().lower()
    if content.startswith(("http://", "https://", "www.")):
        return QRCodeType.URL

    return QRCodeType.UNKNOWN


def parse_portuguese_invoice_qr(qr_data: QRCodeData) -> tuple[Optional[QRExtractedMetadata], Optional[dict]]:
    """Parse Portuguese invoice QR code and extract metadata."""
    try:
        content = qr_data.raw_content.strip()
        parts = content.split("*")

        fields = {}
        for part in parts:
            if ":" not in part:
                continue
            key, _, value = part.partition(":")
            fields[key.strip()] = value.strip()

        if "A" not in fields:
            logger.debug("Missing required field A (issuer NIF)")
            return None, None

        def parse_float(key: str, default: float = 0.0) -> float:
            try:
                return float(fields.get(key, default))
            except (ValueError, TypeError):
                return default

        parsed = PortugueseInvoiceQR(
            issuer_nif=fields.get("A", ""),
            buyer_nif=fields.get("B"),
            country_code=fields.get("C", "PT"),
            document_type_code=fields.get("D", ""),
            document_status=fields.get("E", "N"),
            document_date=fields.get("F", ""),
            document_number=fields.get("G", ""),
            atcud=fields.get("H", ""),
            tax_base_exempt=parse_float("I1"),
            tax_base_reduced=parse_float("I2"),
            tax_reduced=parse_float("I3"),
            tax_base_intermediate=parse_float("I4"),
            tax_intermediate=parse_float("I5"),
            tax_base_normal=parse_float("I6"),
            tax_normal=parse_float("I7"),
            tax_base_stamp=parse_float("I8"),
            total_tax=parse_float("N"),
            gross_total=parse_float("O"),
            withholding_tax=parse_float("P"),
            hash_code=fields.get("Q", ""),
            certificate_number=fields.get("R", ""),
            extra_fields={k: v for k, v in fields.items()
                         if k not in ("A", "B", "C", "D", "E", "F", "G", "H",
                                     "I1", "I2", "I3", "I4", "I5", "I6", "I7", "I8",
                                     "N", "O", "P", "Q", "R")},
        )

        raw_data = {
            "qr_type": "portuguese_invoice",
            "raw_content": qr_data.raw_content,
            "page_number": qr_data.page_number,
        }
        return parsed.to_extracted_metadata(), raw_data
    except Exception as e:
        logger.warning(f"Failed to parse Portuguese invoice QR: {e}")
        return None, None

# Lazy import pyzbar to allow graceful degradation
_pyzbar_available = None
_pyzbar_decode = None


def _find_zbar_library() -> Optional[str]:
    """
    Find the zbar library path.

    Returns:
        Path to the zbar library, or None if not found
    """
    import ctypes.util

    # First try standard library finding
    lib = ctypes.util.find_library('zbar')
    if lib:
        return lib

    # On macOS with Homebrew, check common locations
    if sys.platform == 'darwin':
        zbar_paths = [
            '/opt/homebrew/lib/libzbar.dylib',  # Apple Silicon
            '/opt/homebrew/lib/libzbar.0.dylib',
            '/usr/local/lib/libzbar.dylib',     # Intel Mac
            '/usr/local/lib/libzbar.0.dylib',
        ]
        for lib_path in zbar_paths:
            if Path(lib_path).exists():
                return lib_path

    # On Linux, check common locations
    elif sys.platform.startswith('linux'):
        linux_paths = [
            '/usr/lib/libzbar.so',
            '/usr/lib/x86_64-linux-gnu/libzbar.so',
            '/usr/lib/aarch64-linux-gnu/libzbar.so',
        ]
        for lib_path in linux_paths:
            if Path(lib_path).exists():
                return lib_path

    return None


_original_find_library = None


def _setup_pyzbar_library():
    """
    Set up pyzbar to use the correct zbar library path.

    This patches ctypes.util.find_library to return the correct path for zbar
    on systems where it's not in a standard location.
    Must be called BEFORE importing pyzbar.pyzbar.
    """
    global _original_find_library

    lib_path = _find_zbar_library()
    if not lib_path:
        return False

    try:
        import ctypes
        import ctypes.util

        # Store original find_library if not already stored
        if _original_find_library is None:
            _original_find_library = ctypes.util.find_library

        # Create a patched find_library that returns our path for zbar
        def patched_find_library(name):
            if name == 'zbar':
                return lib_path
            return _original_find_library(name)

        # Apply the patch
        ctypes.util.find_library = patched_find_library
        logger.debug(f"Patched find_library to return {lib_path} for zbar")

        return True
    except Exception as e:
        logger.debug(f"Failed to setup pyzbar library: {e}")
        return False


def _get_pyzbar_decode():
    """Lazy load pyzbar.decode function."""
    global _pyzbar_available, _pyzbar_decode

    if _pyzbar_available is None:
        # Set up zbar library before importing pyzbar
        _setup_pyzbar_library()

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


def check_pyzbar_available() -> tuple[bool, str]:
    """
    Check if pyzbar is available and properly configured.

    Returns:
        Tuple of (is_available, error_message)
    """
    # Check if we can find the zbar library
    lib_path = _find_zbar_library()
    if not lib_path:
        return False, (
            "zbar shared library not found. Install it:\n"
            "  macOS: brew install zbar\n"
            "  Linux: apt install libzbar0"
        )

    # Set up pyzbar to use the library
    _setup_pyzbar_library()

    try:
        from pyzbar.pyzbar import decode
        # Try a minimal decode to verify zbar library works
        from PIL import Image
        test_img = Image.new('RGB', (10, 10), color='white')
        decode(test_img)
        return True, ""
    except ImportError as e:
        return False, f"pyzbar package not installed: {e}"
    except Exception as e:
        error_msg = str(e)
        if "Unable to find zbar shared library" in error_msg:
            return False, (
                "zbar shared library not found. Install it:\n"
                "  macOS: brew install zbar\n"
                "  Linux: apt install libzbar0\n"
                f"Library found at: {lib_path}\n"
                "But pyzbar couldn't load it. Try reinstalling pyzbar."
            )
        return False, f"pyzbar initialization failed: {e}"


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

    # Decode QR codes, suppressing zbar's C-level warnings about partial barcode matches
    try:
        # Suppress stderr to hide zbar's internal warnings (e.g., DataBar assertion failures)
        # These warnings are harmless - they occur when zbar sees patterns that partially
        # match barcodes but can't fully decode them
        stderr_fd = sys.stderr.fileno()
        old_stderr = os.dup(stderr_fd)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, stderr_fd)
        try:
            decoded_objects = decode(img)
        finally:
            os.dup2(old_stderr, stderr_fd)
            os.close(old_stderr)
            os.close(devnull)
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


def extract_metadata_from_qr(pdf_path: Path) -> tuple[Optional[QRExtractedMetadata], Optional[dict]]:
    """
    Main entry point: Extract metadata from QR codes in a PDF.

    Scans the PDF for QR codes, parses them using the appropriate handler,
    and returns extracted metadata along with raw QR data.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        Tuple of (QRExtractedMetadata, raw_data_dict) if a useful QR code was found
        and parsed, (None, None) otherwise. raw_data_dict contains:
        - qr_type: handler name identifying the parser used
        - raw_content: original QR string as decoded
        - page_number: page where QR was found
    """
    qr_codes = extract_all_qr_codes(pdf_path)

    if not qr_codes:
        return None, None

    # Try to extract metadata from each QR code (prioritize structured formats over URLs)
    # Sort by priority: Portuguese invoice > other structured > URL > unknown
    priority_order = {
        QRCodeType.PORTUGUESE_INVOICE: 0,
        QRCodeType.URL: 10,
        QRCodeType.UNKNOWN: 20,
    }
    qr_codes.sort(key=lambda x: priority_order.get(x.qr_type, 15))

    for qr_data in qr_codes:
        if is_portuguese_invoice_qr(qr_data.raw_content):
            metadata, raw_data = parse_portuguese_invoice_qr(qr_data)
            if metadata:
                logger.debug(f"Extracted metadata from {qr_data.qr_type.value} QR: "
                           f"date={metadata.issue_date}, type={metadata.document_type}, "
                           f"amount={metadata.total_amount}")
                return metadata, raw_data

    return None, None
