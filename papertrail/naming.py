"""Filename construction helpers."""

import re
import unicodedata

from papertrail.models import DocumentMetadata


def sanitize_filename_component(value: str) -> str:
    """Sanitize a string for use in a filename."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r'[\\/*?:"<>|()\[\],]', "", value).strip()
    return re.sub(r"\s+", " ", value)


def file_name_from_metadata(metadata: DocumentMetadata, file_hash: str) -> str:
    """Generate a filename from document metadata."""
    parts = [
        sanitize_filename_component(metadata.date_issued),
        sanitize_filename_component(metadata.document_type),
        sanitize_filename_component(metadata.issuing_party),
    ]

    if metadata.document_title:
        title = sanitize_filename_component(metadata.document_title)
        if len(title) > 80:
            title = title[:80].rsplit(" ", 1)[0]
        parts.append(title)

    if metadata.total_amount is not None:
        amount = (
            f"{metadata.total_amount:.0f}"
            if metadata.total_amount.is_integer()
            else f"{metadata.total_amount:.2f}"
        )
        currency = metadata.total_amount_currency or ""
        parts.append(sanitize_filename_component(f"{amount} {currency}".strip()))

    ext = getattr(metadata, "source_extension", None) or ".pdf"
    parts.append(f"{file_hash}{ext}")
    return " - ".join(parts).lower()
