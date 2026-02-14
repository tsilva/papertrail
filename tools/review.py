"""Gradio document review tool for auditing export folders."""

import base64
import html as html_lib
import json
import re
import sys
import warnings
from pathlib import Path

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # PyMuPDF
import gradio as gr
import openpyxl

from papertrail.profiles import load_profile

# ── Module-level cache (single-user local tool) ─────────────────

_CACHE = {"data": {}}

# ── JS: clickable file links in transaction table → update dropdown ──

_JS = """
window.closePreviewPanel = function() {
    var panel = document.getElementById('preview_panel');
    if (panel) panel.classList.remove('expanded');
};

window.selectReviewFile = function(filename) {
    var panel = document.getElementById('preview_panel');
    if (panel && !panel.classList.contains('expanded')) {
        panel.classList.add('expanded');
        _ensureDragHandle(panel);
    }

    var el = document.getElementById('selected_file_bridge');
    if (!el) return;
    var input = el.querySelector('textarea') || el.querySelector('input');
    if (!input) {
        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') input = el;
        else return;
    }
    var newVal = filename + '|' + Date.now();
    var proto = input.tagName === 'TEXTAREA'
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    var setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
    setter.call(input, newVal);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
};

/* ── Drag support ── */
function _ensureDragHandle(panel) {
    if (panel.querySelector('.preview-drag-bar')) return;
    var bar = document.createElement('div');
    bar.className = 'preview-drag-bar';
    var grip = document.createElement('span');
    grip.className = 'drag-grip';
    grip.textContent = '\\u2261\\u2261';
    var closeBtn = document.createElement('button');
    closeBtn.className = 'preview-close-btn';
    closeBtn.textContent = '\\u2715';
    closeBtn.onclick = function(e) { e.stopPropagation(); closePreviewPanel(); };
    bar.appendChild(closeBtn);
    bar.appendChild(grip);
    panel.insertBefore(bar, panel.firstChild);

    var ox, oy, sx, sy;
    bar.addEventListener('mousedown', function(e) {
        if (e.target === closeBtn) return;
        e.preventDefault();
        ox = e.clientX; oy = e.clientY;
        var rect = panel.getBoundingClientRect();
        sx = rect.left; sy = rect.top;
        function onMove(ev) {
            panel.style.left = (sx + ev.clientX - ox) + 'px';
            panel.style.top = (sy + ev.clientY - oy) + 'px';
            panel.style.right = 'auto';
        }
        function onUp() {
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });

    /* ── Resize handle (left edge) ── */
    var resizeBar = document.createElement('div');
    resizeBar.className = 'preview-resize-bar';
    panel.appendChild(resizeBar);

    resizeBar.addEventListener('mousedown', function(e) {
        e.preventDefault();
        e.stopPropagation();
        var startX = e.clientX;
        var startW = panel.offsetWidth;
        document.body.style.cursor = 'ew-resize';
        document.body.style.userSelect = 'none';
        function onMove(ev) {
            var newW = Math.max(360, startW - (ev.clientX - startX));
            panel.style.width = newW + 'px';
        }
        function onUp() {
            document.body.style.cursor = '';
            document.body.style.userSelect = '';
            document.removeEventListener('mousemove', onMove);
            document.removeEventListener('mouseup', onUp);
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
}

/* ── Fullscreen image on click ── */
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
    overflow: visible !important;
    height: 100vh !important; display: flex !important; flex-direction: column !important;
}
.gradio-container > .main { flex: 1 !important; min-height: 0 !important; display: flex !important; flex-direction: column !important; }
.gradio-container > .main > .wrap { flex: 1 !important; min-height: 0 !important; display: flex !important; flex-direction: column !important; }
#content_row { flex: 1 !important; min-height: 0 !important; }
#bank_html { flex: 1 !important; min-height: 0 !important; overflow-y: auto !important; }
.file-link {
    color: #58a6ff; text-decoration: underline; cursor: pointer;
    background: none; border: none; font: inherit; padding: 0;
}
.file-link:hover { color: #79c0ff; }
.bank-section {
    border: 1px solid var(--border-color-primary, #444);
    border-radius: 8px; overflow: hidden;
    margin-bottom: 16px;
}
.bank-info {
    padding: 6px 16px; font-size: 13px;
    color: var(--body-text-color-subdued, #aaa);
}
.bank-info strong { color: var(--body-text-color, #ddd); }
.txn-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.txn-table th {
    background: var(--table-even-background-fill, #333); padding: 6px 8px;
    text-align: left; position: sticky; top: 0; z-index: 1;
    color: var(--body-text-color, #ddd); font-weight: 600;
    border-bottom: 2px solid var(--border-color-primary, #555);
    white-space: nowrap;
}
.txn-table td {
    padding: 5px 8px; border-bottom: 1px solid var(--border-color-primary, #333);
    white-space: nowrap;
}
.txn-table td:last-child { white-space: normal; word-break: break-all; }
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
#selected_file_bridge {
    position: fixed !important; left: -9999px !important;
    width: 1px !important; height: 1px !important;
}
#preview_panel { display: none !important; }
#preview_panel.expanded {
    display: flex !important;
    flex-direction: column;
    position: fixed !important;
    top: 20px; right: 20px;
    width: 520px;
    max-height: calc(100vh - 40px);
    overflow-y: auto;
    z-index: 1000;
    background: var(--background-fill-primary, #1a1a1a);
    border: 1px solid var(--border-color-primary, #444);
    border-radius: 8px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.4);
    padding: 12px;
}
.preview-drag-bar {
    display: flex; align-items: center; justify-content: flex-end;
    cursor: grab; user-select: none;
    border-bottom: 1px solid var(--border-color-primary, #444);
    margin: -4px -4px 8px; padding: 6px 8px;
}
.preview-drag-bar:active { cursor: grabbing; }
.drag-grip {
    color: var(--body-text-color-subdued, #666); font-size: 14px; letter-spacing: 2px;
}
.preview-close-btn {
    background: none; border: none; cursor: pointer;
    color: var(--body-text-color-subdued, #888); font-size: 16px;
    padding: 2px 6px; border-radius: 4px; line-height: 1;
}
.preview-close-btn:hover {
    background: rgba(220, 53, 69, 0.3); color: #ff6b6b;
}
.preview-resize-bar {
    position: absolute; left: 0; top: 0; bottom: 0; width: 5px;
    cursor: ew-resize; z-index: 10;
}
.preview-resize-bar:hover, .preview-resize-bar:active {
    background: var(--border-color-primary, #555);
}
"""

# ── Color coding for reconciliation status ───────────────────────

_COLORS = {
    "exact": "rgba(40, 167, 69, 0.25)",
    "llm_high": "rgba(40, 167, 69, 0.15)",
    "llm_low": "rgba(255, 193, 7, 0.25)",
    "incomplete": "rgba(255, 152, 0, 0.25)",
    "unmatched": "rgba(220, 53, 69, 0.25)",
    "unclassified": "rgba(220, 53, 69, 0.25)",
}

_LABELS = {
    "exact": "Matched",
    "llm_high": "Matched (LLM)",
    "llm_low": "Low confidence",
    "incomplete": "Incomplete",
    "unmatched": "Unmatched",
    "unclassified": "Unclassified",
}


# ── Export folder discovery ───────────────────────────────────────

def _get_export_base_dir():
    """Get the export base directory from the default profile."""
    try:
        profile = load_profile("default")
        if profile.paths.export:
            p = Path(profile.paths.export)
            if p.is_dir():
                return p
    except Exception:
        pass
    return None


def _list_export_folders(base_dir):
    """List YYYY-MM subdirectories in the export base dir, sorted most recent first."""
    if not base_dir or not base_dir.is_dir():
        return []
    folders = []
    for d in base_dir.iterdir():
        if d.is_dir() and re.match(r"^\d{4}-\d{2}$", d.name):
            folders.append(d.name)
    return sorted(folders, reverse=True)


# ── Data loading ─────────────────────────────────────────────────

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


def load_export_folder(folder_path):
    """Load all JSON sidecars from an export folder."""
    folder = Path(folder_path.strip().strip("'\""))
    if not folder.is_dir():
        return {}, f"**Error:** `{folder_path}` is not a valid directory."

    bank_statements, file_index = [], {}

    for json_path in sorted(folder.rglob("*.json")):
        if json_path.name.endswith(".reconciliation.json"):
            continue
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        except Exception:
            continue

        doc_path = _find_companion(json_path, metadata)
        entry = {
            "json_path": str(json_path),
            "doc_path": str(doc_path) if doc_path else None,
            "metadata": metadata,
        }
        if doc_path:
            file_index[doc_path.name] = entry

        if metadata.get("bank_statement"):
            recon = None
            if doc_path:
                rp = doc_path.with_suffix(".reconciliation.json")
                if rp.exists():
                    try:
                        with open(rp, "r", encoding="utf-8") as f:
                            recon = json.load(f)
                    except Exception:
                        pass
            entry["reconciliation"] = recon
            bank_statements.append(entry)

    bank_statements.sort(key=lambda b: Path(b["doc_path"]).name if b.get("doc_path") else "")

    # Collect bank-related filenames for the bank tab dropdown
    bank_files = set()
    for bs in bank_statements:
        recon = bs.get("reconciliation")
        if recon:
            for m in recon.get("matches", []):
                for f in m.get("files", []):
                    if f in file_index:
                        bank_files.add(f)

    # Aggregate unmatched files from all reconciliation results (deduplicate by filename)
    unmatched_files_map: dict[str, dict] = {}
    for bs in bank_statements:
        recon = bs.get("reconciliation")
        if recon:
            for uf in recon.get("unmatched_files", []):
                fname = uf.get("file", "")
                if fname and fname not in unmatched_files_map:
                    unmatched_files_map[fname] = uf
    unmatched_files = sorted(unmatched_files_map.values(), key=lambda u: u.get("file", ""))

    n_recon = sum(1 for b in bank_statements if b.get("reconciliation"))
    status = f"Loaded **{len(bank_statements)}** bank statements"
    if n_recon:
        status += f" ({n_recon} with reconciliation)"
    status += f", **{len(file_index)}** files indexed"
    if unmatched_files:
        status += f", **{len(unmatched_files)}** unmatched files"

    return {
        "bank_statements": bank_statements,
        "bank_files": sorted(bank_files),
        "file_index": file_index,
        "unmatched_files": unmatched_files,
        "folder_path": str(folder),
    }, status


# ── Rendering helpers ────────────────────────────────────────────

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


def render_xlsx_as_html(xlsx_path, max_rows=100):
    """Render XLSX worksheet as HTML table."""
    warnings.filterwarnings("ignore", message="Workbook contains no default style")
    try:
        wb = openpyxl.load_workbook(str(xlsx_path), data_only=True)
        ws = wb.active
        lines = [
            '<div>',
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


def _match_status(m):
    """Classify a reconciliation match into a status category."""
    if m.get("errors") or m.get("warnings"):
        return "incomplete"
    if m.get("method") == "exact":
        return "exact"
    if m.get("method") == "llm":
        return "llm_high" if m.get("confidence", 0) >= 0.8 else "llm_low"
    return "exact"


def render_single_bank_html(bs):
    """Render a single bank statement entry as HTML with info line and transaction table."""
    if not bs:
        return '<p class="placeholder">Select a bank statement.</p>'

    meta = bs.get("metadata", {})
    recon = bs.get("reconciliation")
    bs_data = meta.get("bank_statement", {})

    parts = ['<div class="bank-section">']

    # Info line
    info = []
    if bs_data.get("account_number"):
        info.append(f'Account: <strong>{bs_data["account_number"]}</strong>')
    if bs_data.get("period_start") and bs_data.get("period_end"):
        info.append(f'Period: {bs_data["period_start"]} to {bs_data["period_end"]}')
    if bs_data.get("transaction_count"):
        info.append(f'{bs_data["transaction_count"]} transactions')
    if info:
        parts.append(f'<div class="bank-info">{" &middot; ".join(info)}</div>')

    # Reconciliation
    if recon:
        s = recon.get("summary", {})
        reconciled = s.get("reconciled", s.get("matched", 0))
        rate = s.get("reconciliation_rate", s.get("match_rate", 0))
        inc = f' &middot; {s.get("incomplete", 0)} incomplete' if s.get("incomplete") else ""
        parts.append(
            f'<div class="bank-info"><strong>Reconciliation:</strong> '
            f'{reconciled}/{s.get("total", 0)} reconciled '
            f'({rate:.1f}%){inc}</div>'
        )
        parts.append(_render_txn_table(recon))
    else:
        parts.append(
            '<div class="bank-info" style="color:var(--body-text-color-subdued,#666);">'
            'No reconciliation data.</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)


def render_all_banks_html(bs_list, unmatched_files=None, file_index=None):
    """Render all bank statements as concatenated HTML sections."""
    if not bs_list:
        return '<p class="placeholder">No bank statements found in this folder.</p>'
    parts = [render_single_bank_html(bs) for bs in bs_list]
    if unmatched_files:
        parts.append(_render_unmatched_files_html(unmatched_files, file_index or {}))
    return "\n".join(parts)


def _render_unmatched_files_html(unmatched_files, file_index):
    """Render an unmatched files section with a table of clickable file links."""
    if not unmatched_files:
        return ""

    parts = ['<div class="bank-section unmatched-section">']
    parts.append(
        f'<div class="bank-info" style="background:rgba(220,53,69,0.12);">'
        f'<strong>Unmatched Files ({len(unmatched_files)})</strong>'
        f'</div>'
    )

    parts.append('<div style="margin:0 16px 12px;">')
    parts.append("<table class='txn-table'><thead><tr>")
    for h, a in [
        ("Date", "left"), ("Issuing Party", "left"), ("Type", "left"),
        ("Amount", "right"), ("File", "left"),
    ]:
        parts.append(f'<th style="text-align:{a};">{h}</th>')
    parts.append("</tr></thead><tbody>")

    for uf in unmatched_files:
        fname = uf.get("file", "")
        date = uf.get("date_issued", "") or ""
        party = html_lib.escape(uf.get("issuing_party", "") or "")
        doc_type = html_lib.escape(uf.get("document_type", "") or "")
        amt = uf.get("total_amount")
        currency = uf.get("currency", "EUR") or "EUR"
        amt_str = f"{amt:.2f} {currency}" if amt is not None else "-"

        sf = fname.replace("'", "\\'")
        file_cell = (
            f'<button class="file-link" onclick="selectReviewFile(\'{sf}\');'
            f'return false;">{html_lib.escape(fname)}</button>'
        )

        bg = _COLORS["unmatched"]
        parts.append(f'<tr style="background:{bg};">')
        parts.append(f"<td>{date}</td><td>{party}</td><td>{doc_type}</td>")
        parts.append(f'<td style="text-align:right;font-family:monospace;">{amt_str}</td>')
        parts.append(f"<td>{file_cell}</td>")
        parts.append("</tr>")

    parts.append("</tbody></table></div>")
    parts.append("</div>")
    return "\n".join(parts)


def _render_txn_table(recon):
    """Render reconciliation transactions as a color-coded HTML table."""
    matches = recon.get("matches", [])
    unmatched = recon.get("unmatched", [])

    rows = []
    for m in matches:
        rows.append({**m, "_st": _match_status(m)})
    for u in unmatched:
        rows.append({
            **u, "_st": "unmatched", "files": [], "confidence": 0,
            "method": "", "reasoning": "", "warnings": [],
        })
    # Unclassified always takes priority — red regardless of match state
    for r in rows:
        if r.get("transaction_category") == "unclassified":
            r["_st"] = "unclassified"
    rows.sort(key=lambda r: r.get("row", 0))

    lines = ['<div style="margin:0 16px 12px;">']
    lines.append("<table class='txn-table'><thead><tr>")
    for h, a in [
        ("Row", "left"), ("Date", "left"), ("Description", "left"),
        ("Amount", "right"), ("Rule", "left"), ("Status", "left"), ("Conf.", "center"), ("Files", "left"),
    ]:
        lines.append(f'<th style="text-align:{a};">{h}</th>')
    lines.append("</tr></thead><tbody>")

    for r in rows:
        bg = _COLORS.get(r["_st"], "transparent")
        if r["_st"] == "incomplete":
            errs = r.get("errors", [])
            lab = "; ".join(errs) if errs else _LABELS["incomplete"]
        else:
            lab = _LABELS.get(r["_st"], "")
        conf = r.get("confidence", 0)
        cs = f"{conf:.0%}" if conf > 0 else "-"
        amt = f'{r.get("amount", 0):.2f}'
        desc = html_lib.escape(r.get("description", ""))
        date = r.get("date", "") or ""

        rule = html_lib.escape(r.get("transaction_category", "") or "")

        # Tooltip with reasoning/errors
        tip_parts = []
        if r.get("reasoning"):
            tip_parts.append(r["reasoning"])
        for key in ("errors", "warnings"):
            if r.get(key):
                tip_parts.append(", ".join(r[key]))
        tip = html_lib.escape(" | ".join(tip_parts))

        # Clickable file links
        file_list = r.get("files", [])
        if file_list:
            fs = ""
            for f in file_list:
                sf = f.replace("'", "\\'")
                fs += (
                    f'<p style="margin:0;"><button class="file-link" onclick="selectReviewFile(\'{sf}\');'
                    f'return false;">{html_lib.escape(f)}</button></p>'
                )
        else:
            fs = '<span style="color:var(--body-text-color-subdued,#555);">-</span>'

        lines.append(f'<tr style="background:{bg};" title="{tip}">')
        lines.append(f"<td>{r.get('row', '')}</td><td>{date}</td>")
        lines.append(
            f'<td style="max-width:250px;overflow:hidden;text-overflow:ellipsis;'
            f'white-space:nowrap;">{desc}</td>'
        )
        lines.append(
            f'<td style="text-align:right;font-family:monospace;">{amt}</td>'
        )
        lines.append(f"<td>{rule}</td><td>{lab}</td><td style='text-align:center;'>{cs}</td><td>{fs}</td>")
        lines.append("</tr>")

    lines.append("</tbody></table></div>")
    return "\n".join(lines)


# ── Preview logic ────────────────────────────────────────────────

_EMPTY_PREVIEW = (
    '<p class="placeholder">Select a file to preview.</p>',
    "", "Page -/-", 0,
)


def _do_preview(filename, page):
    """Preview a file using cached data. Returns (preview_html, json_str, page_label, page_num)."""
    data = _CACHE["data"]
    if not filename or not data:
        return _EMPTY_PREVIEW

    # Strip timestamp suffix from JS bridge
    if "|" in filename:
        filename = filename.rsplit("|", 1)[0]

    entry = data.get("file_index", {}).get(filename)
    if not entry:
        return (
            f'<p class="placeholder">File not found: {html_lib.escape(filename)}</p>',
            "", "Page -/-", 0,
        )

    doc_path = entry.get("doc_path")
    metadata = entry.get("metadata", {})
    json_str = json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True)

    if not doc_path or not Path(doc_path).exists():
        return ('<p class="placeholder">File not found on disk.</p>',
                json_str, "No file", 0)

    p = Path(doc_path)
    if p.suffix.lower() == ".pdf":
        preview_html, total, clamped = _render_pdf_page_html(doc_path, page)
        pl = f"Page {clamped + 1}/{total}" if total else "Page -/-"
        return (preview_html, json_str, pl, clamped)
    elif p.suffix.lower() == ".xlsx":
        return (render_xlsx_as_html(doc_path), json_str, "XLSX", 0)
    else:
        return (
            f'<p class="placeholder">Unsupported: {p.suffix}</p>',
            json_str, "Page -/-", 0,
        )


# ── Gradio UI ────────────────────────────────────────────────────

def build_ui():
    # Discover export folders from profile
    export_base = _get_export_base_dir()
    folder_choices = _list_export_folders(export_base)
    default_folder = folder_choices[0] if folder_choices else None

    with gr.Blocks(title="Papertrail Review") as app:
        # State (small values only — large data lives in _CACHE)
        export_base_state = gr.State(str(export_base) if export_base else "")
        bank_page = gr.State(0)
        bank_file = gr.State("")

        # Header
        gr.Markdown("## Papertrail Document Review")

        folder_dd = gr.Dropdown(
            label="Export Folder",
            choices=folder_choices,
            value=default_folder,
            interactive=True,
        )
        status_bar = gr.Markdown("")

        # Hidden JS bridge textbox (positioned off-screen via CSS)
        selected_file_bridge = gr.Textbox(
            elem_id="selected_file_bridge", label="", container=False,
        )

        with gr.Row(equal_height=False, elem_id="content_row"):
            with gr.Column(scale=1, min_width=400):
                bank_html = gr.HTML(
                    '<p class="placeholder">Load a folder to view bank statements.</p>',
                    elem_id="bank_html",
                )
            with gr.Column(scale=1, min_width=400, elem_id="preview_panel"):
                with gr.Tabs():
                    with gr.Tab("Preview"):
                        bank_preview = gr.HTML(
                            '<p class="placeholder">Select a file to preview.</p>'
                        )
                        with gr.Row():
                            b_prev = gr.Button("< Prev", size="sm")
                            b_page = gr.Markdown("Page -/-")
                            b_next = gr.Button("Next >", size="sm")
                    with gr.Tab("Raw JSON"):
                        bank_json = gr.Code(language="json", label="")

        # ── Load folder handler ──────────────────────────────────

        def on_load(folder_name, base_dir):
            empty = (
                "Select an export folder.",
                '<p class="placeholder">No data loaded.</p>',
            )
            if not folder_name or not base_dir:
                _CACHE["data"] = {}
                return empty
            folder_path = str(Path(base_dir) / folder_name)
            data, status = load_export_folder(folder_path)
            if not data:
                _CACHE["data"] = {}
                return (status, empty[1])

            _CACHE["data"] = data

            bs_list = data.get("bank_statements", [])
            bank_content = render_all_banks_html(
                bs_list,
                unmatched_files=data.get("unmatched_files"),
                file_index=data.get("file_index"),
            )

            return (status, bank_content)

        folder_dd.change(
            on_load, [folder_dd, export_base_state],
            [status_bar, bank_html],
        )

        # ── Bank: file selection via JS bridge (click in table) ──

        def on_bridge_input(raw_value):
            if not raw_value:
                return *_EMPTY_PREVIEW, ""
            filename = raw_value.rsplit("|", 1)[0] if "|" in raw_value else raw_value
            preview, js_str, pl, pg = _do_preview(filename, 0)
            return preview, js_str, pl, pg, filename

        selected_file_bridge.change(
            on_bridge_input, [selected_file_bridge],
            [bank_preview, bank_json, b_page, bank_page, bank_file],
        )

        # ── Bank: page navigation ────────────────────────────────

        def on_bank_prev(pg, fn):
            preview, _j, pl, p = _do_preview(fn, max(0, pg - 1))
            return preview, pl, p

        def on_bank_next(pg, fn):
            preview, _j, pl, p = _do_preview(fn, pg + 1)
            return preview, pl, p

        b_prev.click(
            on_bank_prev, [bank_page, bank_file],
            [bank_preview, b_page, bank_page],
        )
        b_next.click(
            on_bank_next, [bank_page, bank_file],
            [bank_preview, b_page, bank_page],
        )


        # ── Auto-load most recent folder on startup ──────────────

        if default_folder:
            app.load(
                on_load, [folder_dd, export_base_state],
                [status_bar, bank_html],
            )

    return app


demo = build_ui()

if __name__ == "__main__":
    demo.launch(css=_CSS, js=_JS)
