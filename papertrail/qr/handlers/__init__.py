"""QR code handler registry and detection."""

from typing import Optional

from papertrail.logging_utils import get_logger
from papertrail.qr.handlers.base import QRHandler
from papertrail.qr.handlers.portuguese_invoice import PortugueseInvoiceHandler
from papertrail.qr.models import QRCodeType

logger = get_logger('qr.handlers')

# Registry of all available handlers (order matters - first match wins)
_HANDLERS: list[QRHandler] = [
    PortugueseInvoiceHandler(),
]


def detect_qr_type(raw_content: str) -> QRCodeType:
    """
    Detect the type of QR code from its content.

    Args:
        raw_content: Raw string content from the QR code

    Returns:
        QRCodeType enum value
    """
    if not raw_content:
        return QRCodeType.UNKNOWN

    # Check each handler
    for handler in _HANDLERS:
        if handler.can_handle(raw_content):
            if isinstance(handler, PortugueseInvoiceHandler):
                return QRCodeType.PORTUGUESE_INVOICE

    # Check for URL pattern
    content = raw_content.strip().lower()
    if content.startswith(("http://", "https://", "www.")):
        return QRCodeType.URL

    return QRCodeType.UNKNOWN


def get_handler(qr_type: QRCodeType) -> Optional[QRHandler]:
    """
    Get the appropriate handler for a QR code type.

    Args:
        qr_type: Type of QR code

    Returns:
        Handler instance or None if no handler available
    """
    handler_map = {
        QRCodeType.PORTUGUESE_INVOICE: PortugueseInvoiceHandler,
    }

    handler_class = handler_map.get(qr_type)
    if handler_class:
        # Return singleton from registry
        for handler in _HANDLERS:
            if isinstance(handler, handler_class):
                return handler

    return None


def get_handler_for_content(raw_content: str) -> Optional[QRHandler]:
    """
    Get the appropriate handler for raw QR content.

    Args:
        raw_content: Raw string content from the QR code

    Returns:
        Handler instance or None if no handler can process this content
    """
    for handler in _HANDLERS:
        if handler.can_handle(raw_content):
            return handler
    return None


__all__ = [
    "QRHandler",
    "PortugueseInvoiceHandler",
    "detect_qr_type",
    "get_handler",
    "get_handler_for_content",
]
