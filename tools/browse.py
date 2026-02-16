"""Gradio document browser for viewing processed documents."""

import base64
import html as html_lib
import json
import sys
import warnings
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # PyMuPDF
import gradio as gr
import openpyxl

from papertrail.config import load_profile

# ── Module-level cache (single-user local tool) ─────────────────

_CACHE = {
    "entries": [],       # list of index entry dicts
    "processed_dir": "",
}

# ── JS: click bridge + keyboard navigation + fullscreen ─────────

_JS = """
window.selectBrowseEntry = function(idx) {
    var el = document.getElementById('selected_entry_bridge');
    if (!el) return;
    var input = el.querySelector('textarea') || el.querySelector('input');
    if (!input) {
        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') input = el;
        else return;
    }
    var newVal = idx + '|' + Date.now();
    var proto = input.tagName === 'TEXTAREA'
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(input, newVal);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
};

/* Highlight selected row */
window._highlightRow = function(idx) {
    document.querySelectorAll('.browse-row').forEach(function(r) {
        r.classList.remove('browse-row-selected');
    });
    var sel = document.querySelector('.browse-row[data-idx="' + idx + '"]');
    if (sel) {
        sel.classList.add('browse-row-selected');
        sel.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
};

/* Keyboard navigation */
document.addEventListener('keydown', function(e) {
    /* Skip when typing in search */
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    var overlay = document.querySelector('.preview-fullscreen-overlay');
    if (e.key === 'Escape' && overlay) { overlay.remove(); return; }
    if (e.key === 'ArrowDown' || e.key === 'j') {
        e.preventDefault();
        document.querySelector('#next_result_btn button')?.click();
    }
    if (e.key === 'ArrowUp' || e.key === 'k') {
        e.preventDefault();
        document.querySelector('#prev_result_btn button')?.click();
    }
});

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

_CSS = """
.gradio-container, .gradio-container[class*="gradio-container-"] {
    max-width: 100% !important; padding-left: 4px !important; padding-right: 4px !important;
    height: 100vh !important; display: flex !important; flex-direction: column !important;
}
.gradio-container > .main { flex: 1 !important; min-height: 0 !important; display: flex !important; flex-direction: column !important; }
.gradio-container > .main > .wrap { flex: 1 !important; min-height: 0 !important; display: flex !important; flex-direction: column !important; }
#content_row { flex: 1 !important; min-height: 0 !important; overflow: hidden !important; }
#results_col { overflow-y: auto !important; max-height: calc(100vh - 160px) !important; }
#preview_col { overflow-y: auto !important; max-height: calc(100vh - 160px) !important; }
.preview-img { max-height: 70vh; width: auto; object-fit: contain; }
#selected_entry_bridge {
    position: fixed !important; left: -9999px !important;
    width: 1px !important; height: 1px !important;
}
#prev_result_btn, #next_result_btn {
    position: fixed !important; left: -9999px !important;
    width: 1px !important; height: 1px !important;
}
.browse-row {
    padding: 8px 12px; cursor: pointer;
    border-bottom: 1px solid var(--border-color-primary, #333);
    border-left: 3px solid transparent;
    transition: background 0.1s;
}
.browse-row:hover { background: var(--table-even-background-fill, #2a2a2a); }
.browse-row-selected {
    background: var(--table-even-background-fill, #2a2a2a) !important;
    border-left: 3px solid #58a6ff !important;
}
.browse-row .row-date {
    font-size: 12px; color: var(--body-text-color-subdued, #888);
}
.browse-row .row-type {
    display: inline-block; font-size: 11px; font-weight: 600;
    padding: 1px 6px; border-radius: 3px; margin-right: 4px;
    background: rgba(88, 166, 255, 0.15); color: #58a6ff;
}
.browse-row .row-party {
    font-size: 13px; color: var(--body-text-color, #ddd); font-weight: 500;
}
.browse-row .row-title {
    font-size: 12px; color: var(--body-text-color-subdued, #aaa);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.browse-row .row-amount {
    font-size: 12px; font-family: monospace;
    color: var(--body-text-color-subdued, #aaa);
}
.browse-row .row-filename {
    font-size: 11px; color: var(--body-text-color-subdued, #666);
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    max-width: 100%;
}
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
.stats-bar {
    font-size: 13px; color: var(--body-text-color-subdued, #aaa);
    padding: 4px 0;
}
"""


# ── Helpers ──────────────────────────────────────────────────────

def _get_processed_dir():
    """Get the processed directory from the default profile."""
    try:
        profile = load_profile("default")
        if profile.paths.processed:
            p = Path(profile.paths.processed)
            if p.is_dir():
                return str(p)
    except Exception:
        pass
    return ""


def _is_internal_path(path):
    """Check if a path is inside internal directories that should be skipped."""
    parts = path.parts
    for part in parts:
        if part.startswith("_") or part == "logs":
            return True
    return False


def _find_companion(json_path, metadata):
    """Find companion document file for a JSON sidecar."""
    ext = metadata.get("source_extension")
    if ext:
        c = json_path.with_suffix(ext)
        if c.exists():
            return c
    for ext in (".pdf", ".xlsx"):
        c = json_path.with_suffix(ext)
        if c.exists():
            return c
    return None


def _render_pdf_page_html(pdf_path, page_num=0):
    """Render a single PDF page as an HTML img tag. Returns (html, total_pages, clamped_page)."""
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


def _render_xlsx_as_html(xlsx_path, max_rows=100):
    """Render XLSX worksheet as HTML table."""
    warnings.filterwarnings("ignore", message="Workbook contains no default style")
    try:
        wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
        ws = wb.active
        lines = [
            '<div style="overflow-x:auto;">',
            '<table style="border-collapse:collapse;font-size:13px;width:100%;">',
        ]
        n = 0
        for row in ws.iter_rows(values_only=True):
            if n >= max_rows:
                lines.append(
                    '<tr><td colspan="10" style="text-align:center;padding:8px;'
                    'color:var(--body-text-color-subdued,#888);">... truncated ...</td></tr>'
                )
                break
            bg = "var(--table-even-background-fill,#2a2a2a)" if n % 2 == 0 else "var(--table-odd-background-fill,#333)"
            lines.append(f'<tr style="background:{bg};">')
            for cell in row:
                v = html_lib.escape(str(cell)) if cell is not None else ""
                lines.append(
                    f'<td style="border:1px solid var(--border-color-primary,#444);'
                    f'padding:3px 6px;white-space:nowrap;'
                    f'color:var(--body-text-color,#ddd);">{v}</td>'
                )
            lines.append("</tr>")
            n += 1
        lines.append("</table></div>")
        wb.close()
        return "\n".join(lines)
    except Exception as e:
        return f'<p class="placeholder">Error reading XLSX: {html_lib.escape(str(e))}</p>'


# ── Data loading ─────────────────────────────────────────────────

def _load_entries(processed_dir):
    """Scan processed directory and build lightweight index entries."""
    root = Path(processed_dir)
    if not root.is_dir():
        return []

    entries = []
    for json_path in root.rglob("*.json"):
        # Skip internal paths
        try:
            rel = json_path.relative_to(root)
        except ValueError:
            continue
        if _is_internal_path(rel):
            continue
        if json_path.name.endswith(".reconciliation.json"):
            continue

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception:
            continue

        # Build search text from key fields
        search_parts = [
            json_path.stem,
            metadata.get("document_type", ""),
            metadata.get("issuing_party", ""),
            metadata.get("document_title", "") or "",
            metadata.get("date_issued", "") or "",
            metadata.get("issuing_party_raw", "") or "",
            metadata.get("document_type_raw", "") or "",
        ]
        search_text = " ".join(str(p) for p in search_parts).lower()

        entries.append({
            "json_path": str(json_path),
            "metadata": metadata,
            "search_text": search_text,
        })

    # Sort by date_issued descending, then filename
    entries.sort(
        key=lambda e: (e["metadata"].get("date_issued") or "0000-00-00", e["json_path"]),
        reverse=True,
    )

    return entries


# ── Rendering ────────────────────────────────────────────────────

def _render_results_html(entries, selected_idx=0):
    """Render the results list as HTML rows."""
    if not entries:
        return '<p class="placeholder">No documents found.</p>'

    parts = []
    for i, entry in enumerate(entries):
        meta = entry["metadata"]
        date = meta.get("date_issued") or ""
        doc_type = html_lib.escape(meta.get("document_type") or "")
        party = html_lib.escape(meta.get("issuing_party") or "")
        title = html_lib.escape(meta.get("document_title") or "")
        amount = meta.get("total_amount")
        currency = meta.get("total_amount_currency") or ""
        filename = Path(entry["json_path"]).stem

        amount_str = ""
        if amount is not None:
            amount_str = f"{amount} {currency}".strip()

        selected_class = " browse-row-selected" if i == selected_idx else ""

        parts.append(
            f'<div class="browse-row{selected_class}" data-idx="{i}" '
            f'onclick="selectBrowseEntry({i})">'
            f'<div class="row-date">{date}</div>'
            f'<div><span class="row-type">{doc_type}</span>'
            f'<span class="row-party">{party}</span></div>'
        )
        if title:
            parts.append(f'<div class="row-title">{title}</div>')
        if amount_str:
            parts.append(f'<div class="row-amount">{amount_str}</div>')
        parts.append(
            f'<div class="row-filename" title="{html_lib.escape(filename)}">'
            f'{html_lib.escape(filename)}</div>'
            f'</div>'
        )

    return "\n".join(parts)


def _stats_text(shown, total):
    """Build stats text like '247 of 512 documents'."""
    if shown == total:
        return f"{total} documents"
    return f"{shown} of {total} documents"


# ── Preview logic ────────────────────────────────────────────────

_EMPTY_PREVIEW = (
    '<p class="placeholder">Select a document to preview.</p>',
    "", "Page -/-", 0,
)


def _do_preview(entry, page_num=0):
    """Preview a document entry. Returns (preview_html, json_str, page_label, clamped_page)."""
    if not entry:
        return _EMPTY_PREVIEW

    metadata = entry["metadata"]
    json_str = json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True)
    json_path = Path(entry["json_path"])
    doc_path = _find_companion(json_path, metadata)

    if not doc_path or not doc_path.exists():
        return (
            '<p class="placeholder">Companion file not found on disk.</p>',
            json_str, "No file", 0,
        )

    if doc_path.suffix.lower() == ".pdf":
        preview_html, total, clamped = _render_pdf_page_html(doc_path, page_num)
        pl = f"Page {clamped + 1}/{total}" if total else "Page -/-"
        return preview_html, json_str, pl, clamped
    elif doc_path.suffix.lower() == ".xlsx":
        return _render_xlsx_as_html(doc_path), json_str, "XLSX", 0
    else:
        return (
            f'<p class="placeholder">Unsupported format: {doc_path.suffix}</p>',
            json_str, "Page -/-", 0,
        )


# ── Gradio UI ────────────────────────────────────────────────────

def build_ui():
    processed_dir = _get_processed_dir()

    with gr.Blocks(title="Papertrail Document Browser") as app:
        # State
        current_idx = gr.State(0)
        page_state = gr.State(0)
        filtered_indices = gr.State([])  # indices into _CACHE["entries"]

        gr.Markdown("## Papertrail Document Browser")

        with gr.Row():
            search_box = gr.Textbox(
                label="Search",
                placeholder="Type to filter documents...",
                scale=4,
            )
            stats_md = gr.Markdown("", elem_classes=["stats-bar"])

        # Hidden JS bridge
        selected_entry_bridge = gr.Textbox(
            elem_id="selected_entry_bridge", label="", container=False,
        )

        # Hidden nav buttons for keyboard
        with gr.Row():
            prev_result_btn = gr.Button("prev", elem_id="prev_result_btn", size="sm")
            next_result_btn = gr.Button("next", elem_id="next_result_btn", size="sm")

        with gr.Row(equal_height=False, elem_id="content_row"):
            with gr.Column(scale=1, min_width=320, elem_id="results_col"):
                results_html = gr.HTML(
                    '<p class="placeholder">Loading documents...</p>',
                )
            with gr.Column(scale=2, min_width=400, elem_id="preview_col"):
                with gr.Tabs():
                    with gr.Tab("Preview"):
                        preview_html = gr.HTML(
                            '<p class="placeholder">Select a document to preview.</p>'
                        )
                        with gr.Row():
                            b_prev = gr.Button("< Prev", size="sm")
                            b_page = gr.Markdown("Page -/-")
                            b_next = gr.Button("Next >", size="sm")
                    with gr.Tab("Raw JSON"):
                        json_view = gr.Code(language="json", label="")

        # ── Load on startup ─────────────────────────────────────

        def on_load():
            entries = _load_entries(processed_dir)
            _CACHE["entries"] = entries
            _CACHE["processed_dir"] = processed_dir

            indices = list(range(len(entries)))
            shown = [entries[i] for i in indices]
            stats = _stats_text(len(shown), len(entries))
            results = _render_results_html(shown, selected_idx=0)

            if entries:
                preview, js, pl, pg = _do_preview(entries[0], 0)
            else:
                preview, js, pl, pg = _EMPTY_PREVIEW

            return results, stats, preview, js, pl, pg, 0, indices

        app.load(
            on_load, [],
            [results_html, stats_md, preview_html, json_view,
             b_page, page_state, current_idx, filtered_indices],
        )

        # ── Search handler ──────────────────────────────────────

        def on_search(query):
            entries = _CACHE["entries"]
            query = (query or "").strip().lower()

            if not query:
                indices = list(range(len(entries)))
            else:
                terms = query.split()
                indices = [
                    i for i, e in enumerate(entries)
                    if all(t in e["search_text"] for t in terms)
                ]

            shown = [entries[i] for i in indices]
            stats = _stats_text(len(shown), len(entries))
            results = _render_results_html(shown, selected_idx=0)

            if shown:
                preview, js, pl, pg = _do_preview(shown[0], 0)
            else:
                preview, js, pl, pg = _EMPTY_PREVIEW

            return results, stats, preview, js, pl, pg, 0, indices

        search_box.change(
            on_search, [search_box],
            [results_html, stats_md, preview_html, json_view,
             b_page, page_state, current_idx, filtered_indices],
        )

        # ── Click selection via JS bridge ───────────────────────

        def on_bridge_input(raw_value, indices):
            if not raw_value:
                return *_EMPTY_PREVIEW, 0
            # Parse index from "idx|timestamp"
            idx_str = raw_value.rsplit("|", 1)[0] if "|" in raw_value else raw_value
            try:
                local_idx = int(idx_str)
            except ValueError:
                return *_EMPTY_PREVIEW, 0

            entries = _CACHE["entries"]
            if not indices or local_idx < 0 or local_idx >= len(indices):
                return *_EMPTY_PREVIEW, 0

            global_idx = indices[local_idx]
            entry = entries[global_idx]
            preview, js, pl, pg = _do_preview(entry, 0)
            return preview, js, pl, pg, local_idx

        selected_entry_bridge.change(
            on_bridge_input, [selected_entry_bridge, filtered_indices],
            [preview_html, json_view, b_page, page_state, current_idx],
        )

        # ── Keyboard result navigation ──────────────────────────

        def on_prev_result(idx, indices):
            entries = _CACHE["entries"]
            if not indices:
                return *_EMPTY_PREVIEW, 0, ""
            new_idx = max(0, idx - 1)
            global_idx = indices[new_idx]
            entry = entries[global_idx]
            preview, js, pl, pg = _do_preview(entry, 0)

            shown = [entries[i] for i in indices]
            results = _render_results_html(shown, selected_idx=new_idx)
            return preview, js, pl, pg, new_idx, results

        def on_next_result(idx, indices):
            entries = _CACHE["entries"]
            if not indices:
                return *_EMPTY_PREVIEW, 0, ""
            new_idx = min(len(indices) - 1, idx + 1)
            global_idx = indices[new_idx]
            entry = entries[global_idx]
            preview, js, pl, pg = _do_preview(entry, 0)

            shown = [entries[i] for i in indices]
            results = _render_results_html(shown, selected_idx=new_idx)
            return preview, js, pl, pg, new_idx, results

        prev_result_btn.click(
            on_prev_result, [current_idx, filtered_indices],
            [preview_html, json_view, b_page, page_state, current_idx, results_html],
        )
        next_result_btn.click(
            on_next_result, [current_idx, filtered_indices],
            [preview_html, json_view, b_page, page_state, current_idx, results_html],
        )

        # ── PDF page navigation ─────────────────────────────────

        def on_page_prev(pg, idx, indices):
            entries = _CACHE["entries"]
            if not indices or idx < 0 or idx >= len(indices):
                return *_EMPTY_PREVIEW[:1], _EMPTY_PREVIEW[2], 0
            global_idx = indices[idx]
            entry = entries[global_idx]
            preview, _js, pl, new_pg = _do_preview(entry, max(0, pg - 1))
            return preview, pl, new_pg

        def on_page_next(pg, idx, indices):
            entries = _CACHE["entries"]
            if not indices or idx < 0 or idx >= len(indices):
                return *_EMPTY_PREVIEW[:1], _EMPTY_PREVIEW[2], 0
            global_idx = indices[idx]
            entry = entries[global_idx]
            preview, _js, pl, new_pg = _do_preview(entry, pg + 1)
            return preview, pl, new_pg

        b_prev.click(
            on_page_prev, [page_state, current_idx, filtered_indices],
            [preview_html, b_page, page_state],
        )
        b_next.click(
            on_page_next, [page_state, current_idx, filtered_indices],
            [preview_html, b_page, page_state],
        )

    return app


demo = build_ui()

if __name__ == "__main__":
    demo.launch(css=_CSS, js=_JS)
