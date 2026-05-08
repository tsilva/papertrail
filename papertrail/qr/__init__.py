"""QR extraction models and entry points."""

from importlib import import_module

__all__ = [
    "QRCodeType",
    "QRCodeData",
    "QRExtractedMetadata",
    "PortugueseInvoiceQR",
    "extract_qr_codes_from_page",
    "extract_all_qr_codes",
    "extract_metadata_from_qr",
    "extract_all_metadata_from_qr",
    "check_pyzbar_available",
    "detect_qr_type",
]

_EXPORTS = {
    "QRCodeType": ("papertrail.qr.models", "QRCodeType"),
    "QRCodeData": ("papertrail.qr.models", "QRCodeData"),
    "QRExtractedMetadata": ("papertrail.qr.models", "QRExtractedMetadata"),
    "PortugueseInvoiceQR": ("papertrail.qr.models", "PortugueseInvoiceQR"),
    "extract_qr_codes_from_page": ("papertrail.qr.extractor", "extract_qr_codes_from_page"),
    "extract_all_qr_codes": ("papertrail.qr.extractor", "extract_all_qr_codes"),
    "extract_metadata_from_qr": ("papertrail.qr.extractor", "extract_metadata_from_qr"),
    "extract_all_metadata_from_qr": ("papertrail.qr.extractor", "extract_all_metadata_from_qr"),
    "check_pyzbar_available": ("papertrail.qr.extractor", "check_pyzbar_available"),
    "detect_qr_type": ("papertrail.qr.extractor", "detect_qr_type"),
}


def __getattr__(name: str):
    try:
        module_name, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted((*globals(), *__all__))
