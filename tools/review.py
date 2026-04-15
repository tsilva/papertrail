"""Gradio document review tool for auditing export folders."""

import html as html_lib
import json
import re
from pathlib import Path

import gradio as gr

if __package__:
    from .shared import (
        FULLSCREEN_CSS,
        FULLSCREEN_JS,
        bridge_value,
        find_companion,
        get_export_dir,
        iter_sidecars,
        placeholder_html,
        render_document_preview,
    )
else:
    from shared import (  # type: ignore
        FULLSCREEN_CSS,
        FULLSCREEN_JS,
        bridge_value,
        find_companion,
        get_export_dir,
        iter_sidecars,
        placeholder_html,
        render_document_preview,
    )

_CACHE = {"data": {}}

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

def _get_export_base_dir():
    export_dir = get_export_dir()
    if not export_dir:
        return None
    path = Path(export_dir)
    return path if path.is_dir() else None


def _list_export_folders(base_dir):
    if not base_dir or not base_dir.is_dir():
        return []
    folders = []
    for d in base_dir.iterdir():
        if d.is_dir() and re.match(r"^\d{4}-\d{2}$", d.name):
            folders.append(d.name)
    return sorted(folders, reverse=True)


def _collect_referenced_files(bank_statements):
    referenced = set()
    for bank_statement in bank_statements:
        recon = bank_statement.get("reconciliation")
        if not recon:
            continue
        for match in recon.get("matches", []):
            referenced.update(f for f in match.get("files", []) if f)
        for unmatched_file in recon.get("unmatched_files", []):
            filename = unmatched_file.get("file", "")
            if filename:
                referenced.add(filename)
    return referenced


def _build_cross_folder_index(folder, file_index, referenced_files):
    export_base = _get_export_base_dir()
    if not export_base or export_base == folder or not referenced_files:
        return {}

    missing_files = {
        filename for filename in referenced_files
        if filename not in file_index
    }
    if not missing_files:
        return {}

    cross_file_index = {}
    for json_path, metadata in iter_sidecars(export_base):
        if json_path.name.endswith(".reconciliation.json"):
            continue

        doc_path = find_companion(json_path, metadata)
        if not doc_path:
            continue

        filename = doc_path.name
        if filename not in missing_files or filename in cross_file_index:
            continue

        cross_file_index[filename] = {
            "json_path": str(json_path),
            "doc_path": str(doc_path),
            "metadata": metadata,
        }
        if len(cross_file_index) == len(missing_files):
            break

    return cross_file_index


def load_export_folder(folder_path):
    folder = Path(folder_path.strip().strip("'\""))
    if not folder.is_dir():
        return {}, f"**Error:** `{folder_path}` is not a valid directory."

    bank_statements, file_index = [], {}

    for json_path, metadata in iter_sidecars(folder):
        if json_path.name.endswith(".reconciliation.json"):
            continue
        doc_path = find_companion(json_path, metadata)
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
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        pass
            entry["reconciliation"] = recon
            bank_statements.append(entry)

    bank_statements.sort(key=lambda b: Path(b["doc_path"]).name if b.get("doc_path") else "")

    bank_files = set()
    for bs in bank_statements:
        recon = bs.get("reconciliation")
        if recon:
            for m in recon.get("matches", []):
                bank_files.update(f for f in m.get("files", []) if f)

    referenced_files = _collect_referenced_files(bank_statements)
    cross_file_index = _build_cross_folder_index(folder, file_index, referenced_files)

    unmatched_files_map: dict[str, dict] = {}
    for bs in bank_statements:
        recon = bs.get("reconciliation")
        if recon:
            for uf in recon.get("unmatched_files", []):
                fname = uf.get("file", "")
                if fname and fname not in unmatched_files_map:
                    unmatched_files_map[fname] = uf
    unmatched_files = sorted(
        (uf for uf in unmatched_files_map.values() if uf.get("file", "") not in bank_files),
        key=lambda u: u.get("file", ""),
    )

    n_recon = sum(1 for b in bank_statements if b.get("reconciliation"))
    status = f"Loaded **{len(bank_statements)}** bank statements"
    if n_recon:
        status += f" ({n_recon} with reconciliation)"
    status += f", **{len(file_index)}** files indexed"
    if cross_file_index:
        status += f", **{len(cross_file_index)}** cross-folder files indexed"
    if unmatched_files:
        status += f", **{len(unmatched_files)}** unmatched files"

    return {
        "bank_statements": bank_statements,
        "bank_files": sorted(bank_files),
        "file_index": file_index,
        "cross_file_index": cross_file_index,
        "unmatched_files": unmatched_files,
        "folder_path": str(folder),
    }, status

def _match_status(m):
    if m.get("errors") or m.get("warnings"):
        return "incomplete"
    if m.get("method") == "exact":
        return "exact"
    if m.get("method") == "llm":
        return "llm_high" if m.get("confidence", 0) >= 0.8 else "llm_low"
    return "exact"


def render_single_bank_html(bs):
    if not bs:
        return placeholder_html("Select a bank statement.")

    meta = bs.get("metadata", {})
    recon = bs.get("reconciliation")
    bs_data = meta.get("bank_statement", {})

    parts = ['<div class="bank-section">']

    info = []
    if bs_data.get("account_number"):
        info.append(f'Account: <strong>{bs_data["account_number"]}</strong>')
    if bs_data.get("period_start") and bs_data.get("period_end"):
        info.append(f'Period: {bs_data["period_start"]} to {bs_data["period_end"]}')
    if bs_data.get("transaction_count"):
        info.append(f'{bs_data["transaction_count"]} transactions')
    if info:
        parts.append(f'<div class="bank-info">{" &middot; ".join(info)}</div>')

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
    if not bs_list:
        return placeholder_html("No bank statements found in this folder.")
    parts = [render_single_bank_html(bs) for bs in bs_list]
    if unmatched_files:
        parts.append(_render_unmatched_files_html(unmatched_files, file_index or {}))
    return "\n".join(parts)


def _render_unmatched_files_html(unmatched_files, file_index):
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

        tip_parts = []
        if r.get("reasoning"):
            tip_parts.append(r["reasoning"])
        for key in ("errors", "warnings"):
            if r.get(key):
                tip_parts.append(", ".join(r[key]))
        tip = html_lib.escape(" | ".join(tip_parts))

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

_EMPTY_PREVIEW = (
    placeholder_html("Select a file to preview."),
    "", "Page -/-", 0,
)


def _do_preview(filename, page):
    """Preview a file using cached data. Returns (preview_html, json_str, page_label, page_num)."""
    data = _CACHE["data"]
    if not filename or not data:
        return _EMPTY_PREVIEW

    filename = bridge_value(filename)

    entry = data.get("file_index", {}).get(filename)
    if not entry:
        entry = data.get("cross_file_index", {}).get(filename)
    if not entry:
        return (
            placeholder_html(f"File not found: {filename}"),
            "", "Page -/-", 0,
        )

    doc_path = entry.get("doc_path")
    metadata = entry.get("metadata", {})
    json_str = json.dumps(metadata, indent=2, ensure_ascii=False, sort_keys=True)

    if not doc_path or not Path(doc_path).exists():
        return placeholder_html("File not found on disk."), json_str, "No file", 0

    preview_html, label, page_num = render_document_preview(doc_path, page)
    return preview_html, json_str, label, page_num

def build_ui():
    export_base = _get_export_base_dir()
    folder_choices = _list_export_folders(export_base)
    default_folder = folder_choices[0] if folder_choices else None

    with gr.Blocks(title="Papertrail Review") as app:
        export_base_state = gr.State(str(export_base) if export_base else "")
        bank_page = gr.State(0)
        bank_file = gr.State("")

        gr.Markdown("## Papertrail Document Review")

        folder_dd = gr.Dropdown(
            label="Export Folder",
            choices=folder_choices,
            value=default_folder,
            interactive=True,
        )
        status_bar = gr.Markdown("")

        selected_file_bridge = gr.Textbox(
            elem_id="selected_file_bridge", label="", container=False,
        )

        with gr.Row(equal_height=False, elem_id="content_row"):
            with gr.Column(scale=1, min_width=400):
                bank_html = gr.HTML(
                    placeholder_html("Load a folder to view bank statements."),
                    elem_id="bank_html",
                )
            with gr.Column(scale=1, min_width=400, elem_id="preview_panel"):
                with gr.Tabs():
                    with gr.Tab("Preview"):
                        bank_preview = gr.HTML(_EMPTY_PREVIEW[0])
                        with gr.Row():
                            b_prev = gr.Button("< Prev", size="sm")
                            b_page = gr.Markdown("Page -/-")
                            b_next = gr.Button("Next >", size="sm")
                    with gr.Tab("Raw JSON"):
                        bank_json = gr.Code(language="json", label="")

        def on_load(folder_name, base_dir):
            empty = (
                "Select an export folder.",
                placeholder_html("No data loaded."),
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

        def on_bridge_input(raw_value):
            if not raw_value:
                return *_EMPTY_PREVIEW, ""
            filename = bridge_value(raw_value)
            preview, js_str, pl, pg = _do_preview(filename, 0)
            return preview, js_str, pl, pg, filename

        selected_file_bridge.change(
            on_bridge_input, [selected_file_bridge],
            [bank_preview, bank_json, b_page, bank_page, bank_file],
        )

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

        if default_folder:
            app.load(
                on_load, [folder_dd, export_base_state],
                [status_bar, bank_html],
            )

    return app

if __name__ == "__main__":
    build_ui().launch(css="\n".join([FULLSCREEN_CSS, _CSS]), js="\n".join([FULLSCREEN_JS, _JS]))
