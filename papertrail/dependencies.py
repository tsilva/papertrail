"""Runtime dependency validation."""

from __future__ import annotations

from importlib import import_module

from papertrail.config import ConfigError
from papertrail.qr import check_pyzbar_available

_REQUIRED_IMPORTS = [
    ("archex", "archex"),
    ("yaml", "pyyaml"),
    ("fitz", "PyMuPDF"),
    ("pandas", "pandas"),
    ("openpyxl", "openpyxl"),
    ("PIL", "pillow"),
    ("gradio", "gradio"),
    ("orjson", "orjson"),
    ("pikepdf", "pikepdf"),
]


def validate_runtime_dependencies() -> None:
    """Fail fast when required Python or system dependencies are unavailable."""
    failures: list[str] = []

    for module_name, package_name in _REQUIRED_IMPORTS:
        try:
            import_module(module_name)
        except Exception as exc:
            failures.append(f"- {package_name}: {exc}")

    qr_ok, qr_message = check_pyzbar_available()
    if not qr_ok:
        failures.append(f"- pyzbar/zbar: {qr_message}")

    if failures:
        raise ConfigError(
            "Missing required runtime dependencies:\n"
            + "\n".join(failures)
            + "\nInstall dependencies before running papertrail. "
            "Suggested fix: `uv pip install -e .` and install the zbar system library."
        )


__all__ = ["validate_runtime_dependencies"]
