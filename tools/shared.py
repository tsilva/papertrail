"""Shared helpers for Gradio tools."""

from __future__ import annotations

import base64
import html as html_lib
import warnings
from functools import lru_cache
from pathlib import Path

import fitz  # PyMuPDF

from papertrail.config import load_profile
from papertrail.repository import DocumentRepository
from papertrail.runtime import runtime_from_profile


@lru_cache(maxsize=1)
def build_repository() -> DocumentRepository | None:
    try:
        profile = load_profile("default")
    except Exception:
        return None

    if not profile.paths.processed:
        return None

    runtime = runtime_from_profile(
        profile,
        enable_client=False,
        probe_api=False,
    )
    return DocumentRepository(runtime)


def get_processed_dir() -> str:
    try:
        profile = load_profile("default")
        if profile.paths.processed:
            path = Path(profile.paths.processed)
            if path.is_dir():
                return str(path)
    except Exception:
        pass
    return ""


def get_export_dir() -> str:
    try:
        profile = load_profile("default")
        if profile.paths.export:
            path = Path(profile.paths.export)
            if path.is_dir():
                return str(path)
    except Exception:
        pass
    return ""


def find_companion(json_path: Path, metadata: dict) -> Path | None:
    repository = build_repository()
    if repository is None:
        return None
    return repository.find_companion(json_path, metadata)


def is_internal_path(path: Path) -> bool:
    repository = build_repository()
    if repository is None:
        parts = path.parts
        return any(part.startswith("_") or part == "logs" for part in parts)
    return repository.is_internal_path(path)


def iter_sidecars(root: Path):
    repository = build_repository()
    if repository is None:
        return []
    return repository.iter_sidecars(root)


def render_pdf_page_html(pdf_path, page_num=0):
    try:
        with fitz.open(str(pdf_path)) as doc:
            total = len(doc)
            page_num = max(0, min(page_num, total - 1))
            pix = doc[page_num].get_pixmap(dpi=150)
            b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            html = f'<img src="data:image/png;base64,{b64}" class="preview-img"/>'
            return html, total, page_num
    except Exception:
        return '<p class="placeholder">Error rendering PDF.</p>', 0, 0


def render_xlsx_as_html(xlsx_path, max_rows=100):
    import openpyxl

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
            bg = "var(--table-even-background-fill,#2a2a2a)" if row_count % 2 == 0 else "var(--table-odd-background-fill,#333)"
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
        return f'<p class="placeholder">Error reading XLSX: {html_lib.escape(str(exc))}</p>'


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
/* Fullscreen image on click */
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
