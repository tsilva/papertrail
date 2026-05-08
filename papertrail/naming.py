"""Filename construction helpers."""

import re
import unicodedata

from papertrail.models import DocumentMetadata

DEFAULT_COMPONENT_MAX_CHARS = 80


def sanitize_filename_component(value: str) -> str:
    """Sanitize a string for use in a filename."""
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    value = re.sub(r'[\\/*?:"<>|()\[\],]', "", value).strip()
    return re.sub(r"\s+", " ", value)


def trim_filename_component(value: str, max_chars: int = DEFAULT_COMPONENT_MAX_CHARS) -> str:
    """Trim a filename component without cutting the final word when possible."""
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[:max_chars].rsplit(" ", 1)[0] or value[:max_chars]


def file_name_from_metadata(
    metadata: DocumentMetadata,
    file_hash: str,
    *,
    component_max_chars: int = DEFAULT_COMPONENT_MAX_CHARS,
) -> str:
    """Generate a filename from document metadata."""
    parts = [
        sanitize_filename_component(metadata.date_issued),
        sanitize_filename_component(metadata.document_type),
        sanitize_filename_component(metadata.issuing_party),
    ]

    if metadata.document_title:
        title = sanitize_filename_component(metadata.document_title)
        title = trim_filename_component(title, component_max_chars)
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
