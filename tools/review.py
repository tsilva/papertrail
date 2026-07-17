"""Gradio document review tool for auditing export folders."""

import html as html_lib
import json
import re
import tempfile
import zipfile
from pathlib import Path

import gradio as gr

from papertrail.reconciliation_groundtruth import (
    approval_map,
    document_sets_match,
    groundtruth_path_for_document,
    load_groundtruth,
    remove_approval,
    remove_unmatched_file_approval,
    rows_with_transaction_keys,
    unmatched_file_approvals,
    upsert_approval,
    upsert_unmatched_file_approval,
)

if __package__:
    from .shared import (
        FULLSCREEN_CSS,
        FULLSCREEN_JS,
        bridge_value,
        find_companion,
        get_export_dir,
        iter_sidecars,
        launch_blocks,
        placeholder_html,
        profile_setting,
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
        launch_blocks,
        placeholder_html,
        profile_setting,
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

window.setReviewApproval = function(payload) {
    var el = document.getElementById('approval_bridge');
    if (!el) return;
    var input = el.querySelector('textarea') || el.querySelector('input');
    if (!input) {
        if (el.tagName === 'TEXTAREA' || el.tagName === 'INPUT') input = el;
        else return;
    }
    var newVal = JSON.stringify(payload) + '|' + Date.now();
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
.gradio-container > .main {
    flex: 1 !important; min-height: 0 !important;
    display: flex !important; flex-direction: column !important;
}
.gradio-container > .main > .wrap {
    flex: 1 !important; min-height: 0 !important;
    display: flex !important; flex-direction: column !important;
}
#content_row {
    flex: 1 !important; min-height: 0 !important;
    overflow: hidden !important;
    max-width: 100% !important;
}
#bank_html {
    flex: 1 !important; min-height: 0 !important;
    overflow: auto !important;
    max-width: 100% !important;
}
.file-link {
    color: #58a6ff; text-decoration: underline; cursor: pointer;
    background: none; border: none; font: inherit; padding: 0;
}
.file-link:hover { color: #79c0ff; }
.filename-warning {
    color: #ffd166;
    font-weight: 700;
    margin-right: 4px;
}
.approval-btn {
    border: 1px solid var(--border-color-primary, #555);
    border-radius: 6px;
    background: var(--button-secondary-background-fill, #2b2b2b);
    color: var(--body-text-color, #ddd);
    cursor: pointer;
    font: inherit;
    padding: 2px 8px;
}
.approval-btn:hover { border-color: #58a6ff; }
.approval-btn.approved {
    border-color: rgba(40, 167, 69, 0.8);
    color: #7ee787;
}
.approval-btn.changed {
    border-color: rgba(255, 193, 7, 0.8);
    color: #ffd166;
}
.approval-btn.expected {
    border-color: rgba(40, 167, 69, 0.8);
    color: #7ee787;
}
.bank-section {
    border: 1px solid var(--border-color-primary, #444);
    border-radius: 8px; overflow: hidden;
    margin-bottom: 16px;
    max-width: 100%;
}
.bank-info {
    padding: 6px 16px; font-size: 13px;
    color: var(--body-text-color-subdued, #aaa);
}
.bank-info strong { color: var(--body-text-color, #ddd); }
.txn-table-wrap {
    margin: 0 16px 12px;
    max-width: 100%;
    overflow-x: auto;
}
.txn-table {
    width: 100%;
    max-width: 100%;
    table-layout: fixed;
    border-collapse: collapse;
    font-size: 13px;
}
.txn-table th {
    background: var(--table-even-background-fill, #333); padding: 6px 8px;
    text-align: left; position: sticky; top: 0; z-index: 1;
    color: var(--body-text-color, #ddd); font-weight: 600;
    border-bottom: 2px solid var(--border-color-primary, #555);
    white-space: nowrap;
}
.txn-table td {
    padding: 5px 8px; border-bottom: 1px solid var(--border-color-primary, #333);
    min-width: 0;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.txn-table .col-row { width: 44px; }
.txn-table .col-date { width: 80px; }
.txn-table .col-description { width: 260px; }
.txn-table .col-amount { width: 92px; }
.txn-table .col-rule { width: 180px; }
.txn-table .col-status { width: 150px; }
.txn-table .col-approval { width: 110px; }
.txn-table .col-confidence { width: 64px; }
.txn-table .col-files { width: auto; }
.txn-table .col-party { width: 180px; }
.txn-table .col-type { width: 120px; }
.txn-table .wrap-cell {
    white-space: normal;
    overflow-wrap: anywhere;
    word-break: normal;
}
.txn-table .file-cell p { margin: 0; }
.txn-table .file-link {
    display: inline-block;
    max-width: 100%;
    white-space: normal;
    overflow-wrap: anywhere;
    word-break: normal;
    text-align: left;
}
#selected_file_bridge {
    position: fixed !important; left: -9999px !important;
    width: 1px !important; height: 1px !important;
}
#approval_bridge {
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
    "expected_unreconciled": "rgba(40, 167, 69, 0.14)",
    "unclassified": "rgba(220, 53, 69, 0.25)",
}

_LABELS = {
    "exact": "Matched",
    "llm_high": "Matched (LLM)",
    "llm_low": "Low confidence",
    "incomplete": "Incomplete",
    "unmatched": "Unmatched",
    "expected_unreconciled": "Expected",
    "unclassified": "Unclassified",
}

def _get_export_base_dir(export_path: str | Path | None = None):
    export_dir = export_path or get_export_dir()
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


def _export_folder_options(export_path: str | Path | None = None):
    """Return the dropdown base, choices, and default for a base or folder path."""
    export_dir = _get_export_base_dir(export_path)
    if export_dir is None:
        return None, [], None

    folder_choices = _list_export_folders(export_dir)
    if folder_choices:
        return export_dir, folder_choices, folder_choices[0]

    return export_dir.parent, [export_dir.name], export_dir.name


def _resolve_export_folder(folder_name, base_dir) -> Path | None:
    if not folder_name or not base_dir:
        return None

    base = Path(str(base_dir)).expanduser().resolve()
    folder = (base / str(folder_name)).resolve()
    try:
        folder.relative_to(base)
    except ValueError:
        return None
    return folder if folder.is_dir() else None


def _zip_export_folder(folder: Path) -> tuple[Path | None, str]:
    archive_dir = Path(tempfile.gettempdir()) / "papertrail-review-zips"
    archive_dir.mkdir(parents=True, exist_ok=True)
    zip_path = archive_dir / f"{folder.name}.zip"

    file_count = 0
    total_bytes = 0
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            file_count += 1
            total_bytes += path.stat().st_size
            archive.write(path, Path(folder.name) / path.relative_to(folder))

    if file_count == 0:
        try:
            zip_path.unlink()
        except OSError:
            pass
        return None, f"**Error:** `{folder.name}` has no files to export."

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    source_mb = total_bytes / (1024 * 1024)
    return (
        zip_path,
        f"Created **{zip_path.name}** with **{file_count}** files "
        f"({size_mb:.1f} MB zip, {source_mb:.1f} MB source).",
    )


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


def _build_cross_folder_index(folder, file_index, referenced_files, export_base=None):
    export_base = _get_export_base_dir(export_base)
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
        if not isinstance(metadata, dict):
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


def _document_identity(filename, data):
    entry = data.get("file_index", {}).get(filename)
    if not entry:
        entry = data.get("cross_file_index", {}).get(filename)
    metadata = entry.get("metadata", {}) if entry else {}
    return {
        "filename": filename,
        "hash_file": metadata.get("hash_file"),
        "hash_content": metadata.get("hash_content"),
    }


def _current_documents(file_list, data):
    return [_document_identity(filename, data) for filename in file_list if filename]


def _unmatched_file_approval_status(uf, data):
    statement_file = uf.get("_statement_file", "")
    bank_statement = _find_bank_statement(data, statement_file)
    if not bank_statement:
        return ""

    current_document = _document_identity(uf.get("file", ""), data)
    for approval in unmatched_file_approvals(bank_statement.get("groundtruth")):
        if document_sets_match(
            [current_document],
            [approval.get("document", {})],
        ):
            return "approved"
    return ""


def _approval_status(row, approvals, data):
    approval = approvals.get(row.get("_transaction_key_id"))
    if not approval:
        return "", None

    current_documents = _current_documents(row.get("files", []), data)
    approved_documents = approval.get("required_documents", [])
    if not current_documents:
        return "missing", approval
    if document_sets_match(current_documents, approved_documents):
        return "approved", approval
    return "changed", approval


def _find_bank_statement(data, statement_file):
    for bank_statement in data.get("bank_statements", []):
        doc_path = bank_statement.get("doc_path")
        if doc_path and Path(doc_path).name == statement_file:
            return bank_statement
    return None


def _find_reconciliation_row(bank_statement, row_number):
    recon = bank_statement.get("reconciliation") if bank_statement else None
    if not recon:
        return None
    for row in rows_with_transaction_keys(recon):
        if row.get("row") == row_number:
            return row
    return None


def load_export_folder(folder_path, export_base=None):
    folder = Path(folder_path.strip().strip("'\""))
    if not folder.is_dir():
        return {}, f"**Error:** `{folder_path}` is not a valid directory."

    bank_statements, file_index = [], {}

    for json_path, metadata in iter_sidecars(folder):
        if not isinstance(metadata, dict):
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
                gp = groundtruth_path_for_document(doc_path)
                if gp.exists():
                    try:
                        entry["groundtruth"] = load_groundtruth(gp)
                        entry["groundtruth_path"] = str(gp)
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        entry["groundtruth"] = None
                        entry["groundtruth_path"] = str(gp)
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
    cross_file_index = _build_cross_folder_index(
        folder,
        file_index,
        referenced_files,
        export_base,
    )

    unmatched_files_map: dict[str, dict] = {}
    for bs in bank_statements:
        recon = bs.get("reconciliation")
        if recon:
            statement_file = Path(bs["doc_path"]).name if bs.get("doc_path") else ""
            for uf in recon.get("unmatched_files", []):
                fname = uf.get("file", "")
                if fname and fname not in unmatched_files_map:
                    unmatched = dict(uf)
                    unmatched["_statement_file"] = statement_file
                    unmatched_files_map[fname] = unmatched
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
    n_approvals = sum(
        len((b.get("groundtruth") or {}).get("approvals", []))
        for b in bank_statements
    )
    if n_approvals:
        status += f", **{n_approvals}** approved pairings"
    n_unmatched_file_approvals = sum(
        len(unmatched_file_approvals(b.get("groundtruth")))
        for b in bank_statements
    )
    if n_unmatched_file_approvals:
        status += f", **{n_unmatched_file_approvals}** expected unmatched files"

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
        threshold = profile_setting("tools", "llm_high_confidence_threshold", 0.8)
        return "llm_high" if m.get("confidence", 0) >= threshold else "llm_low"
    return "exact"


def render_single_bank_html(bs, data=None):
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
        parts.append(_render_txn_table(recon, bs, data or {}))
    else:
        parts.append(
            '<div class="bank-info" style="color:var(--body-text-color-subdued,#666);">'
            'No reconciliation data.</div>'
        )

    parts.append("</div>")
    return "\n".join(parts)


def render_all_banks_html(bs_list, unmatched_files=None, file_index=None, data=None):
    if not bs_list:
        return placeholder_html("No bank statements found in this folder.")
    parts = [render_single_bank_html(bs, data=data) for bs in bs_list]
    if unmatched_files:
        parts.append(
            _render_unmatched_files_html(unmatched_files, file_index or {}, data=data)
        )
    return "\n".join(parts)


def _filename_length_warning_icon(filename: str) -> str:
    max_chars = profile_setting("naming", "filename_warning_max_chars", 60)
    if Path(filename).suffix.lower() != ".pdf" or len(filename) <= max_chars:
        return ""
    return (
        '<span class="filename-warning" '
        f'title="PDF filename exceeds {max_chars} characters">&#9888;</span>'
    )


def _render_file_link(filename: str) -> str:
    safe_js = filename.replace("\\", "\\\\").replace("'", "\\'")
    warning = _filename_length_warning_icon(filename)
    return (
        f'<button class="file-link" onclick="selectReviewFile(\'{safe_js}\');'
        f'return false;">{warning}{html_lib.escape(filename)}</button>'
    )


def _render_unmatched_files_html(unmatched_files, file_index, data=None):
    if not unmatched_files:
        return ""

    data = data or {}
    approved_count = sum(
        1
        for uf in unmatched_files
        if _unmatched_file_approval_status(uf, data) == "approved"
    )
    remaining_count = len(unmatched_files) - approved_count
    count_label = (
        f"{remaining_count} pending, {approved_count} expected"
        if approved_count
        else str(len(unmatched_files))
    )

    parts = ['<div class="bank-section unmatched-section">']
    parts.append(
        f'<div class="bank-info" style="background:rgba(220,53,69,0.12);">'
        f'<strong>Unmatched Files ({count_label})</strong>'
        f'</div>'
    )

    parts.append('<div class="txn-table-wrap">')
    parts.append("<table class='txn-table'><colgroup>")
    for klass in [
        "col-date",
        "col-party",
        "col-type",
        "col-amount",
        "col-approval",
        "col-files",
    ]:
        parts.append(f'<col class="{klass}">')
    parts.append("</colgroup><thead><tr>")
    for h, a, klass in [
        ("Date", "left", "date"), ("Issuing Party", "left", "party"),
        ("Type", "left", "type"), ("Amount", "right", "amount"),
        ("Approval", "left", "approval"),
        ("File", "left", "files"),
    ]:
        parts.append(f'<th class="col-{klass}" style="text-align:{a};">{h}</th>')
    parts.append("</tr></thead><tbody>")

    for uf in unmatched_files:
        fname = uf.get("file", "")
        date = uf.get("date_issued", "") or ""
        party = html_lib.escape(uf.get("issuing_party", "") or "")
        doc_type = html_lib.escape(uf.get("document_type", "") or "")
        amt = uf.get("total_amount")
        currency = uf.get("currency") or profile_setting(
            "reconciliation",
            "default_currency",
            "EUR",
        )
        amt_str = f"{amt:.2f} {currency}" if amt is not None else "-"

        file_cell = _render_file_link(fname)

        approval_status = _unmatched_file_approval_status(uf, data)
        approval = _unmatched_file_approval_cell(uf, approval_status)
        bg = (
            _COLORS["expected_unreconciled"]
            if approval_status == "approved"
            else _COLORS["unmatched"]
        )
        parts.append(f'<tr style="background:{bg};">')
        parts.append(f"<td>{date}</td><td>{party}</td><td>{doc_type}</td>")
        parts.append(f'<td style="text-align:right;font-family:monospace;">{amt_str}</td>')
        parts.append(f"<td>{approval}</td>")
        parts.append(f'<td class="wrap-cell file-cell">{file_cell}</td>')
        parts.append("</tr>")

    parts.append("</tbody></table></div>")
    parts.append("</div>")
    return "\n".join(parts)


def _unmatched_file_approval_button(uf, action, label, klass="", title=""):
    payload = json.dumps(
        {
            "kind": "unmatched_file",
            "statement_file": uf.get("_statement_file", ""),
            "file": uf.get("file", ""),
            "action": action,
        },
        ensure_ascii=False,
    )
    safe_payload = html_lib.escape(payload, quote=True)
    safe_label = html_lib.escape(label)
    safe_title = html_lib.escape(title, quote=True)
    classes = f"approval-btn {klass}".strip()
    return (
        f"<button class='{classes}' title='{safe_title}' "
        f"onclick='setReviewApproval({safe_payload});return false;'>{safe_label}</button>"
    )


def _unmatched_file_approval_cell(uf, status):
    if not uf.get("_statement_file"):
        return '<span style="color:#ff7b72;">Missing</span>'
    if status == "approved":
        return _unmatched_file_approval_button(
            uf,
            "remove",
            "Approved",
            "expected",
            "Click to mark this file as needing reconciliation review again.",
        )
    return _unmatched_file_approval_button(
        uf,
        "approve",
        "Approve",
        "",
        "Mark this classified file as expected to remain unreconciled.",
    )


def _approval_button(statement_file, row_number, action, label, klass="", title=""):
    payload = json.dumps(
        {"statement_file": statement_file, "row": row_number, "action": action},
        ensure_ascii=False,
    )
    safe_payload = html_lib.escape(payload, quote=True)
    safe_label = html_lib.escape(label)
    safe_title = html_lib.escape(title, quote=True)
    classes = f"approval-btn {klass}".strip()
    return (
        f"<button class='{classes}' title='{safe_title}' "
        f"onclick='setReviewApproval({safe_payload});return false;'>{safe_label}</button>"
    )


def _approval_cell(row, statement_file):
    status = row.get("_approval_status", "")
    row_number = row.get("row")
    has_files = bool(row.get("files"))

    if status == "approved":
        return _approval_button(
            statement_file,
            row_number,
            "remove",
            "Approved",
            "approved",
            "Click to remove this manual approval.",
        )
    if status == "changed" and has_files:
        return _approval_button(
            statement_file,
            row_number,
            "approve",
            "Changed",
            "changed",
            "Current files differ from the saved approval. Click to approve the current pairing.",
        )
    if status == "missing":
        return '<span style="color:#ff7b72;">Missing</span>'
    if has_files:
        return _approval_button(
            statement_file,
            row_number,
            "approve",
            "Approve",
            "",
            "Save this transaction/document pairing as ground truth.",
        )
    return '<span style="color:var(--body-text-color-subdued,#555);">-</span>'


def _render_txn_table(recon, bank_statement=None, data=None):
    rows = rows_with_transaction_keys(recon)
    for row in rows:
        row["_st"] = "unmatched" if not row.get("files") else _match_status(row)

    statement_file = ""
    if bank_statement and bank_statement.get("doc_path"):
        statement_file = Path(bank_statement["doc_path"]).name

    approvals = approval_map((bank_statement or {}).get("groundtruth"))
    data = data or {}
    for r in rows:
        if r.get("transaction_category") == "unclassified":
            r["_st"] = "unclassified"
        r["_approval_status"], _approval = _approval_status(r, approvals, data)

    lines = ['<div class="txn-table-wrap">']
    lines.append("<table class='txn-table'><colgroup>")
    for klass in [
        "col-row", "col-date", "col-description", "col-amount",
        "col-rule", "col-status", "col-approval", "col-confidence", "col-files",
    ]:
        lines.append(f'<col class="{klass}">')
    lines.append("</colgroup><thead><tr>")
    for h, a, klass in [
        ("Row", "left", "row"), ("Date", "left", "date"),
        ("Description", "left", "description"), ("Amount", "right", "amount"),
        ("Rule", "left", "rule"), ("Status", "left", "status"),
        ("Approval", "left", "approval"), ("Conf.", "center", "confidence"),
        ("Files", "left", "files"),
    ]:
        lines.append(f'<th class="col-{klass}" style="text-align:{a};">{h}</th>')
    lines.append("</tr></thead><tbody>")

    for r in rows:
        bg = _COLORS.get(r["_st"], "transparent")
        if r["_st"] == "incomplete":
            errs = r.get("errors", [])
            lab = f'Incomplete ({len(errs)})' if errs else _LABELS["incomplete"]
        else:
            lab = _LABELS.get(r["_st"], "")
        conf = r.get("confidence", 0)
        cs = f"{conf:.0%}" if conf > 0 else "-"
        amt = f'{r.get("amount", 0):.2f}'
        desc = html_lib.escape(r.get("description", ""))
        date = r.get("date", "") or ""

        rule = html_lib.escape(r.get("transaction_category", "") or "")
        approval = _approval_cell(r, statement_file)

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
                fs += f"<p>{_render_file_link(f)}</p>"
        else:
            fs = '<span style="color:var(--body-text-color-subdued,#555);">-</span>'

        lines.append(f'<tr style="background:{bg};" title="{tip}">')
        lines.append(f"<td>{r.get('row', '')}</td><td>{date}</td>")
        lines.append(f"<td>{desc}</td>")
        lines.append(
            f'<td style="text-align:right;font-family:monospace;">{amt}</td>'
        )
        lines.append(
            f"<td>{rule}</td><td>{lab}</td>"
            f"<td>{approval}</td>"
            f"<td style='text-align:center;'>{cs}</td>"
            f'<td class="wrap-cell file-cell">{fs}</td>'
        )
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

def build_ui(export_path: str | Path | None = None):
    export_base, folder_choices, default_folder = _export_folder_options(export_path)

    with gr.Blocks(title="Papertrail Review") as app:
        export_base_state = gr.State(str(export_base) if export_base else "")
        bank_page = gr.State(0)
        bank_file = gr.State("")

        gr.Markdown("## Papertrail Document Review")

        with gr.Row():
            folder_dd = gr.Dropdown(
                label="Export Folder",
                choices=folder_choices,
                value=default_folder,
                interactive=True,
                scale=4,
            )
            zip_btn = gr.Button("Export ZIP", scale=1)
        status_bar = gr.Markdown("")
        zip_file = gr.File(label="Month ZIP", visible=False)

        selected_file_bridge = gr.Textbox(
            elem_id="selected_file_bridge", label="", container=False,
        )
        approval_bridge = gr.Textbox(
            elem_id="approval_bridge", label="", container=False,
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
                gr.update(value=None, visible=False),
            )
            folder = _resolve_export_folder(folder_name, base_dir)
            if folder is None:
                _CACHE["data"] = {}
                return empty
            data, status = load_export_folder(str(folder), base_dir)
            if not data:
                _CACHE["data"] = {}
                return (status, empty[1], empty[2])

            _CACHE["data"] = data

            bs_list = data.get("bank_statements", [])
            bank_content = render_all_banks_html(
                bs_list,
                unmatched_files=data.get("unmatched_files"),
                file_index=data.get("file_index"),
                data=data,
            )

            return (status, bank_content, empty[2])

        folder_dd.change(
            on_load, [folder_dd, export_base_state],
            [status_bar, bank_html, zip_file],
        )

        def on_zip_click(folder_name, base_dir):
            folder = _resolve_export_folder(folder_name, base_dir)
            if folder is None:
                return (
                    "**Error:** select a valid export folder before creating a zip.",
                    gr.update(value=None, visible=False),
                )

            zip_path, status = _zip_export_folder(folder)
            if zip_path is None:
                return status, gr.update(value=None, visible=False)
            return status, gr.update(value=str(zip_path), visible=True)

        zip_btn.click(
            on_zip_click, [folder_dd, export_base_state],
            [status_bar, zip_file],
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

        def on_approval_input(raw_value, folder_name, base_dir):
            if not raw_value:
                return gr.update(), gr.update()

            try:
                payload = json.loads(bridge_value(raw_value))
            except (TypeError, json.JSONDecodeError):
                return "**Error:** invalid approval payload.", gr.update()

            data = _CACHE.get("data") or {}
            if not data and folder_name and base_dir:
                data, _status = load_export_folder(
                    str(Path(base_dir) / folder_name),
                    base_dir,
                )

            statement_file = payload.get("statement_file")
            bank_statement = _find_bank_statement(data, statement_file)
            doc_path = (
                Path(bank_statement["doc_path"])
                if bank_statement and bank_statement.get("doc_path")
                else None
            )
            if not bank_statement or doc_path is None:
                return "**Error:** could not find the selected bank statement.", gr.update()

            groundtruth_path = groundtruth_path_for_document(doc_path)
            action = payload.get("action")
            if payload.get("kind") == "unmatched_file":
                document = _document_identity(payload.get("file", ""), data)
                if not document.get("filename"):
                    return "**Error:** could not find the selected unmatched file.", gr.update()
                if action == "remove":
                    remove_unmatched_file_approval(groundtruth_path, document=document)
                elif action == "approve":
                    upsert_unmatched_file_approval(
                        groundtruth_path,
                        source=statement_file,
                        document=document,
                    )
                else:
                    return "**Error:** unknown approval action.", gr.update()
            else:
                row_number = payload.get("row")
                row = _find_reconciliation_row(bank_statement, row_number)
                if row is None:
                    return "**Error:** could not find the selected reconciliation row.", gr.update()
                if action == "remove":
                    remove_approval(groundtruth_path, row=row)
                elif action == "approve":
                    documents = _current_documents(row.get("files", []), data)
                    if not documents:
                        return "**Error:** unmatched rows cannot be approved.", gr.update()
                    upsert_approval(
                        groundtruth_path,
                        source=statement_file,
                        row=row,
                        documents=documents,
                    )
                else:
                    return "**Error:** unknown approval action.", gr.update()

            folder_path = data.get("folder_path")
            if not folder_path and folder_name and base_dir:
                folder_path = str(Path(base_dir) / folder_name)
            data, status = load_export_folder(folder_path, base_dir)
            _CACHE["data"] = data
            bank_content = render_all_banks_html(
                data.get("bank_statements", []),
                unmatched_files=data.get("unmatched_files"),
                file_index=data.get("file_index"),
                data=data,
            )
            return status, bank_content

        approval_bridge.change(
            on_approval_input,
            [approval_bridge, folder_dd, export_base_state],
            [status_bar, bank_html],
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
                [status_bar, bank_html, zip_file],
            )

    return app


def launch(
    *,
    export_path: str | Path | None = None,
    argv: list[str] | None = None,
):
    """Launch the review UI for an explicit export path or active profile."""
    return launch_blocks(
        build_ui(export_path),
        css="\n".join([FULLSCREEN_CSS, _CSS]),
        js="\n".join([FULLSCREEN_JS, _JS]),
        argv=argv,
    )

if __name__ == "__main__":
    launch()
