"""Base class for QR code handlers."""

from abc import ABC, abstractmethod
from typing import Optional

from papertrail.qr.models import QRCodeData, QRExtractedMetadata


class QRHandler(ABC):
    """Abstract base class for QR code type handlers."""

    @abstractmethod
    def can_handle(self, raw_content: str) -> bool:
        """
        Check if this handler can process the given QR content.

        Args:
            raw_content: Raw string content from the QR code

        Returns:
            True if this handler can process the content
        """
        pass

    @abstractmethod
    def parse(self, qr_data: QRCodeData) -> tuple[Optional[QRExtractedMetadata], Optional[dict]]:
        """
        Parse QR code data and extract metadata.

        Args:
            qr_data: QRCodeData object with raw content and type info

        Returns:
            Tuple of (QRExtractedMetadata, raw_data_dict) if parsing successful,
            (None, None) otherwise. raw_data_dict contains:
            - qr_type: handler name identifying the parser used
            - raw_content: original QR string as decoded
            - page_number: page where QR was found
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Handler name for logging."""
        pass
