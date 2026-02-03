"""Handler for Portuguese invoice QR codes (Portaria 195/2020)."""

from typing import Optional

from papertrail.logging_utils import get_logger
from papertrail.qr.handlers.base import QRHandler
from papertrail.qr.models import QRCodeData, QRCodeType, QRExtractedMetadata, PortugueseInvoiceQR

logger = get_logger('qr.portuguese')


class PortugueseInvoiceHandler(QRHandler):
    """Handler for Portuguese invoice QR codes following Portaria 195/2020 format."""

    @property
    def name(self) -> str:
        return "portuguese_invoice"

    def can_handle(self, raw_content: str) -> bool:
        """
        Check if content matches Portuguese invoice QR format.

        Detection criteria:
        - Starts with 'A:' (issuer NIF field)
        - Contains '*' delimiters
        - Contains 'H:' (ATCUD field, mandatory since 2022)
        """
        if not raw_content:
            return False

        content = raw_content.strip()

        # Must start with A: (issuer NIF)
        if not content.startswith("A:"):
            return False

        # Must use * as delimiter
        if "*" not in content:
            return False

        # Must contain ATCUD field (H:)
        if "*H:" not in content and not content.startswith("H:"):
            return False

        return True

    def parse(self, qr_data: QRCodeData) -> Optional[QRExtractedMetadata]:
        """
        Parse Portuguese invoice QR code and extract metadata.

        Args:
            qr_data: QRCodeData with raw content

        Returns:
            QRExtractedMetadata if parsing successful, None otherwise
        """
        try:
            parsed = self._parse_qr_content(qr_data.raw_content)
            if parsed:
                return parsed.to_extracted_metadata()
        except Exception as e:
            logger.warning(f"Failed to parse Portuguese invoice QR: {e}")

        return None

    def _parse_qr_content(self, raw_content: str) -> Optional[PortugueseInvoiceQR]:
        """
        Parse raw QR content into PortugueseInvoiceQR dataclass.

        Format: A:value*B:value*C:value*...
        """
        content = raw_content.strip()

        # Split by * delimiter
        parts = content.split("*")

        # Parse each field
        fields = {}
        for part in parts:
            if ":" not in part:
                continue
            key, _, value = part.partition(":")
            fields[key.strip()] = value.strip()

        # Validate required field (issuer NIF)
        if "A" not in fields:
            logger.debug(f"Missing required field A (issuer NIF)")
            return None

        # Parse numeric fields with error handling
        def parse_float(key: str, default: float = 0.0) -> float:
            try:
                return float(fields.get(key, default))
            except (ValueError, TypeError):
                return default

        return PortugueseInvoiceQR(
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
