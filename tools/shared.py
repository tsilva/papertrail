from __future__ import annotations

import argparse
import base64
import html as html_lib
import importlib
import os
import socket
import warnings
from functools import lru_cache
from pathlib import Path

import fitz  # PyMuPDF

from papertrail.config import ConfigError, load_profile
from papertrail.repository import DocumentRepository
from papertrail.runtime import runtime_from_profile


@lru_cache(maxsize=8)
def _load_profile(profile_name: str):
    try:
        return load_profile(profile_name)
    except ConfigError:
        return None


def _active_profile_name() -> str:
    return (
        os.environ.get("PAPERTRAIL_UI_PROFILE")
        or os.environ.get("PAPERTRAIL_PROFILE")
        or os.environ.get("PAPERTRAIL_DEFAULT_PROFILE")
        or "default"
    )


def _tool_setting_int(name: str, default: int) -> int:
    env_name = f"PAPERTRAIL_{name.upper()}"
    try:
        return int(os.environ.get(env_name) or "")
    except ValueError:
        pass
    profile = _load_profile(_active_profile_name())
    if profile is None:
        return default
    try:
        return int(getattr(profile.tools, name.lower(), default) or default)
    except (TypeError, ValueError):
        return default


def profile_setting_int(section: str, name: str, default: int) -> int:
    env_name = f"PAPERTRAIL_{name.upper()}"
    try:
        return int(os.environ.get(env_name) or "")
    except ValueError:
        pass
    profile = _load_profile(_active_profile_name())
    if profile is None:
        return default
    settings = getattr(profile, section, None)
    if settings is None:
        return default
    try:
        return int(getattr(settings, name.lower(), default) or default)
    except (TypeError, ValueError):
        return default


def profile_setting_float(section: str, name: str, default: float) -> float:
    env_name = f"PAPERTRAIL_{name.upper()}"
    try:
        return float(os.environ.get(env_name) or "")
    except ValueError:
        pass
    profile = _load_profile(_active_profile_name())
    if profile is None:
        return default
    settings = getattr(profile, section, None)
    if settings is None:
        return default
    try:
        return float(getattr(settings, name.lower(), default) or default)
    except (TypeError, ValueError):
        return default


def profile_setting_str(section: str, name: str, default: str) -> str:
    env_name = f"PAPERTRAIL_{name.upper()}"
    value = os.environ.get(env_name)
    if value:
        return value
    profile = _load_profile(_active_profile_name())
    if profile is None:
        return default
    settings = getattr(profile, section, None)
    if settings is None:
        return default
    return str(getattr(settings, name.lower(), default) or default)


@lru_cache(maxsize=8)
def build_repository(profile_name: str) -> DocumentRepository | None:
    profile = _load_profile(profile_name)
    if profile is None or not profile.paths.processed:
        return None
    runtime = runtime_from_profile(profile, enable_client=False, probe_api=False)
    return DocumentRepository(runtime)


def _get_profile_dir(path_attr: str) -> str:
    profile = _load_profile(_active_profile_name())
    if profile is None:
        return ""

    path_value = getattr(profile.paths, path_attr, None)
    if not path_value:
        return ""

    path = Path(path_value)
    return str(path) if path.is_dir() else ""


def get_processed_dir() -> str:
    return _get_profile_dir("processed")


def get_export_dir() -> str:
    return _get_profile_dir("export")


def find_companion(json_path: Path, metadata: dict) -> Path | None:
    repository = build_repository(_active_profile_name())
    if repository is None:
        return None
    return repository.find_companion(json_path, metadata)


def is_internal_path(path: Path) -> bool:
    repository = build_repository(_active_profile_name())
    if repository is None:
        parts = path.parts
        return any(part.startswith("_") or part == "logs" for part in parts)
    return repository.is_internal_path(path)


def iter_sidecars(root: Path):
    repository = build_repository(_active_profile_name())
    if repository is None:
        return []
    return repository.iter_sidecars(root)


def placeholder_html(message: str) -> str:
    return f'<p class="placeholder">{html_lib.escape(message)}</p>'


def bridge_value(raw_value: str | None) -> str:
    if not raw_value:
        return ""
    return raw_value.rsplit("|", 1)[0] if "|" in raw_value else raw_value

def page_label(page_num: int, total: int) -> str:
    return f"Page {page_num + 1}/{total}" if total else "Page -/-"


def render_pdf_page_html(pdf_path, page_num=0):
    try:
        with fitz.open(str(pdf_path)) as doc:
            total = len(doc)
            page_num = max(0, min(page_num, total - 1))
            pix = doc[page_num].get_pixmap(dpi=_tool_setting_int("preview_dpi", 150))
            b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            html = f'<img src="data:image/png;base64,{b64}" class="preview-img"/>'
            return html, total, page_num
    except Exception:
        return placeholder_html("Error rendering PDF."), 0, 0


def render_xlsx_as_html(xlsx_path, max_rows=None):
    import openpyxl

    if max_rows is None:
        max_rows = _tool_setting_int("xlsx_preview_max_rows", 100)
    warnings.filterwarnings("ignore", message="Workbook contains no default style")
    try:
        workbook = openpyxl.load_workbook(str(xlsx_path), data_only=True)
        worksheet = workbook.active
        lines = [
            '<div style="overflow-x:auto;">',
            '<table style="border-collapse:collapse;font-size:13px;width:100%;">',
        ]
        row_count = 0
        for row in worksheet.iter_rows(values_only=True):
            if row_count >= max_rows:
                lines.append(
                    '<tr><td colspan="10" style="text-align:center;padding:8px;'
                    'color:var(--body-text-color-subdued,#888);">... truncated ...</td></tr>'
                )
                break
            bg = (
                "var(--table-even-background-fill,#2a2a2a)"
                if row_count % 2 == 0
                else "var(--table-odd-background-fill,#333)"
            )
            lines.append(f'<tr style="background:{bg};">')
            for cell in row:
                value = html_lib.escape(str(cell)) if cell is not None else ""
                lines.append(
                    f'<td style="border:1px solid var(--border-color-primary,#444);'
                    f'padding:3px 6px;white-space:nowrap;'
                    f'color:var(--body-text-color,#ddd);">{value}</td>'
                )
            lines.append("</tr>")
            row_count += 1
        lines.append("</table></div>")
        workbook.close()
        return "\n".join(lines)
    except Exception as exc:
        return placeholder_html(f"Error reading XLSX: {exc}")


def render_document_preview(doc_path: str | Path, page_num: int = 0) -> tuple[str, str, int]:
    path = Path(doc_path)
    if not path.exists():
        return placeholder_html("File not found on disk."), "No file", 0

    if path.suffix.lower() == ".pdf":
        preview_html, total, clamped = render_pdf_page_html(path, page_num)
        return preview_html, page_label(clamped, total), clamped

    if path.suffix.lower() == ".xlsx":
        return render_xlsx_as_html(path), "XLSX", 0

    return placeholder_html(f"Unsupported format: {path.suffix}"), "Page -/-", 0


def _pick_random_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _parse_port_arg(value: str) -> int:
    if value == "auto":
        return _pick_random_port()

    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--port must be an integer or 'auto'") from exc

    if port < 1 or port > 65535:
        raise argparse.ArgumentTypeError("--port must be between 1 and 65535")

    return port


def launch_kwargs_from_cli(argv: list[str] | None = None) -> dict:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--port",
        type=_parse_port_arg,
        default=None,
        help="Server port, or 'auto' to bind a random available port.",
    )
    args = parser.parse_args(argv)

    kwargs = {}
    if args.port is not None:
        kwargs["server_port"] = args.port
    return kwargs


def launch_blocks(
    app,
    *,
    css: str | None = None,
    js: str | None = None,
    argv: list[str] | None = None,
):
    return app.launch(css=css, js=js, **launch_kwargs_from_cli(argv))


def launch_tool(initial_tab: str) -> None:
    module_name = initial_tab.replace("-", "_")
    module = importlib.import_module(f"tools.{module_name}")

    css = "\n".join(
        part for part in (FULLSCREEN_CSS, getattr(module, "_CSS", "")) if part
    )
    js = "\n".join(
        part for part in (FULLSCREEN_JS, getattr(module, "_JS", "")) if part
    )
    launch_blocks(module.build_ui(), css=css, js=js, argv=[])


FULLSCREEN_CSS = """
.placeholder {
    color: var(--body-text-color-subdued, #888); font-size: 14px;
    padding: 32px; text-align: center;
}
.preview-img {
    max-width: 100%; border: 1px solid var(--border-color-primary, #444);
    border-radius: 4px; cursor: zoom-in;
}
.preview-fullscreen-overlay {
    position: fixed; inset: 0; z-index: 9999;
    background: rgba(0,0,0,0.9); display: flex;
    align-items: center; justify-content: center;
    cursor: zoom-out;
}
.preview-fullscreen-overlay img {
    max-width: 95vw; max-height: 95vh; object-fit: contain;
    border-radius: 4px;
}
"""

FULLSCREEN_JS = """
document.addEventListener('click', function(e) {
    var img = e.target.closest('.preview-img');
    if (!img) return;
    var overlay = document.createElement('div');
    overlay.className = 'preview-fullscreen-overlay';
    overlay.innerHTML = '<img src="' + img.src + '"/>';
    overlay.onclick = function() { overlay.remove(); };
    document.body.appendChild(overlay);
});
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') {
        var o = document.querySelector('.preview-fullscreen-overlay');
        if (o) o.remove();
    }
});
"""
