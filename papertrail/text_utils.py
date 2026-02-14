"""Shared text-processing utilities."""

import unicodedata


def strip_diacritics(s: str) -> str:
    """Remove diacritics/accents from a string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )
