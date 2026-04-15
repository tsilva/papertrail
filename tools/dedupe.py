"""Gradio duplicate review tool — scan, review, and execute deduplication in one place."""

import html as html_lib
import json
import shutil
from datetime import datetime
from pathlib import Path

import gradio as gr

from papertrail.hashing import PLAN_FILENAME, scan_directory
if __package__:
    from .shared import (
        FULLSCREEN_CSS,
        find_companion,
        get_processed_dir,
        placeholder_html,
        render_document_preview,
    )
else:
    from shared import (  # type: ignore
        FULLSCREEN_CSS,
        find_companion,
        get_processed_dir,
        placeholder_html,
        render_document_preview,
    )

_CACHE = {
    "plan": {},
    "directory": None,
}

_CSS = """
.gradio-container { max-width: 100% !important; padding-left: 4px !important; padding-right: 4px !important; }
.group-container {
    display: flex; flex-wrap: wrap; gap: 16px; justify-content: flex-start;
    padding: 16px 0;
}
.file-card {
    border: 1px solid var(--border-color-primary, #444);
    border-radius: 8px; overflow: hidden; min-width: 260px; max-width: 400px;
    flex: 1 1 300px; background: var(--block-background-fill, #2a2a2a);
}
.file-card .card-header {
    padding: 8px 12px; font-weight: 600; font-size: 13px;
    border-bottom: 1px solid var(--border-color-primary, #444);
}
.badge-keep {
    background: rgba(40, 167, 69, 0.25); color: #4caf50;
}
.badge-dupe {
    background: rgba(220, 53, 69, 0.25); color: #e57373;
}
.file-card .card-body { padding: 8px 12px; }
.file-card .card-filename {
    font-size: 12px; word-break: break-all;
    color: var(--body-text-color, #ddd); margin-bottom: 6px;
}
.file-card .card-meta {
    font-size: 12px; color: var(--body-text-color-subdued, #aaa);
    margin-bottom: 8px;
}
.file-card .card-meta strong { color: var(--body-text-color, #ddd); }
.status-approved { color: #4caf50; font-weight: 600; }
.status-rejected { color: #e57373; font-weight: 600; }
.status-pending { color: var(--body-text-color-subdued, #888); }
"""

_JS = """
document.addEventListener('keydown', function(e) {
    if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
    if (e.key === 'a') document.querySelector('#approve_btn button')?.click();
    if (e.key === 'r') document.querySelector('#reject_btn button')?.click();
    if (e.key === 'ArrowLeft') document.querySelector('#prev_btn button')?.click();
    if (e.key === 'ArrowRight') document.querySelector('#next_btn button')?.click();
});
"""

def _render_file_card(entry, role, directory):
    badge_class = "badge-keep" if role == "KEEP" else "badge-dupe"
    json_name = entry["json"]
    size_kb = entry.get("size_kb")
    hash_content = entry.get("hash_content", "?")
    size_str = f"{size_kb} KB" if size_kb is not None else "? KB"

    json_path = Path(directory) / json_name
    preview_html = ""
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            companion = find_companion(json_path, data)
            if companion:
                preview_html = render_document_preview(companion)[0]
            else:
                preview_html = placeholder_html("No companion file")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            preview_html = placeholder_html("Error loading metadata")
    else:
        preview_html = placeholder_html("JSON not found")

    return (
        f'<div class="file-card">'
        f'<div class="card-header {badge_class}">{role}</div>'
        f'<div class="card-body">'
        f'<div class="card-filename">{html_lib.escape(json_name)}</div>'
        f'<div class="card-meta">'
        f"<strong>{size_str}</strong> &middot; "
        f"hash: <code>{html_lib.escape(str(hash_content))}</code>"
        f"</div>"
        f"{preview_html}"
        f"</div></div>"
    )


def _render_group(index):
    plan = _CACHE["plan"]
    directory = _CACHE["directory"]
    groups = plan.get("groups", [])

    if not groups or index < 0 or index >= len(groups):
        return '<p class="placeholder">No group to display.</p>'

    group = groups[index]
    cards = []

    cards.append(_render_file_card(group["keep"], "KEEP", directory))

    for entry in group.get("move", []):
        cards.append(_render_file_card(entry, "DUPE", directory))

    hash_type = group.get("group_hash_type", "hash_text")
    hash_val = group.get("group_hash", group.get("hash_text", "?"))
    header = (
        f'<div style="margin-bottom:8px;font-size:13px;'
        f'color:var(--body-text-color-subdued,#aaa);">'
        f'{html_lib.escape(hash_type)}: <code>{html_lib.escape(hash_val)}</code> '
        f'&middot; {1 + len(group.get("move", []))} files in group</div>'
    )

    return header + '<div class="group-container">' + "".join(cards) + "</div>"


def _status_text():
    groups = _CACHE["plan"].get("groups", [])
    total = len(groups)
    if total == 0:
        return "No plan loaded."

    approved = sum(1 for g in groups if g.get("decision") == "approved")
    rejected = sum(1 for g in groups if g.get("decision") == "rejected")
    pending = total - approved - rejected

    stats = _CACHE["plan"].get("scan_stats", {})
    scanned = stats.get("scanned", "?")
    skipped = stats.get("skipped_no_hash", stats.get("skipped_no_text_hash", "?"))

    return (
        f"Scanned {scanned} files ({skipped} without any hash) &mdash; "
        f"**{total}** groups &mdash; "
        f'<span class="status-approved">{approved} approved</span>, '
        f'<span class="status-rejected">{rejected} rejected</span>, '
        f'<span class="status-pending">{pending} pending</span>'
    )


def _decision_label(index):
    groups = _CACHE["plan"].get("groups", [])
    if not groups or index < 0 or index >= len(groups):
        return '<span class="status-pending">PENDING</span>'
    d = groups[index].get("decision")
    if d == "approved":
        return '<span class="status-approved">APPROVED</span>'
    elif d == "rejected":
        return '<span class="status-rejected">REJECTED</span>'
    return '<span class="status-pending">PENDING</span>'


def _nav_label(index):
    total = len(_CACHE["plan"].get("groups", []))
    if total == 0:
        return "Group -/-"
    return f"Group {index + 1}/{total}"


def _next_undecided(start, direction=1):
    groups = _CACHE["plan"].get("groups", [])
    total = len(groups)
    if total == 0:
        return start

    idx = start + direction
    while 0 <= idx < total:
        if groups[idx].get("decision") is None:
            return idx
        idx += direction

    return max(0, min(start + direction, total - 1))


def _save_plan():
    plan = _CACHE["plan"]
    directory = _CACHE["directory"]
    if not plan or not directory:
        return

    groups = plan.get("groups", [])
    approved = sum(1 for g in groups if g.get("decision") == "approved")
    rejected = sum(1 for g in groups if g.get("decision") == "rejected")
    plan["summary"].update(
        approved=approved,
        rejected=rejected,
        pending=len(groups) - approved - rejected,
    )

    plan_path = Path(directory) / PLAN_FILENAME
    tmp = plan_path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(plan, f, indent=4, ensure_ascii=False)
    tmp.rename(plan_path)

def on_scan(directory_path):
    directory_path = directory_path.strip().strip("'\"")
    directory = Path(directory_path)

    if not directory.is_dir():
        _CACHE["plan"] = {}
        _CACHE["directory"] = None
        return (
            f"**Error:** `{directory_path}` is not a valid directory.",
            "", placeholder_html("Enter a valid directory and click Scan."),
            gr.update(visible=False),
        )

    old_decisions = {}
    old_plan_path = directory / PLAN_FILENAME
    if old_plan_path.exists():
        try:
            with open(old_plan_path, "r", encoding="utf-8") as f:
                old_plan = json.load(f)
            for group in old_plan.get("groups", []):
                if group.get("decision"):
                    key = group.get("group_hash", group.get("hash_text"))
                    if key:
                        old_decisions[key] = group["decision"]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            pass

    plan_data = scan_directory(directory)

    for group in plan_data["groups"]:
        group["decision"] = old_decisions.get(group["group_hash"])

    _CACHE["plan"] = plan_data
    _CACHE["directory"] = str(directory)

    _save_plan()

    groups = plan_data["groups"]
    if not groups:
        return (
            "Scan complete — no duplicates found.",
            "", placeholder_html("No duplicate groups found."),
            gr.update(visible=False),
        )

    group_html = _render_group(0)
    status = _status_text()
    nav = _nav_label(0)
    decision = _decision_label(0)

    return (
        status, f"{nav} &mdash; {decision}", group_html,
        gr.update(visible=True),
    )


def on_prev(index):
    new_index = max(0, index - 1)
    group_html = _render_group(new_index)
    nav = _nav_label(new_index)
    decision = _decision_label(new_index)
    return new_index, f"{nav} &mdash; {decision}", group_html


def on_next(index):
    total = len(_CACHE["plan"].get("groups", []))
    new_index = min(total - 1, index + 1) if total > 0 else 0
    group_html = _render_group(new_index)
    nav = _nav_label(new_index)
    decision = _decision_label(new_index)
    return new_index, f"{nav} &mdash; {decision}", group_html


def on_approve(index):
    groups = _CACHE["plan"].get("groups", [])
    if groups and 0 <= index < len(groups):
        groups[index]["decision"] = "approved"
        _save_plan()

    new_index = _next_undecided(index, direction=1)
    group_html = _render_group(new_index)
    status = _status_text()
    nav = _nav_label(new_index)
    decision = _decision_label(new_index)
    return new_index, status, f"{nav} &mdash; {decision}", group_html


def on_reject(index):
    groups = _CACHE["plan"].get("groups", [])
    if groups and 0 <= index < len(groups):
        groups[index]["decision"] = "rejected"
        _save_plan()

    new_index = _next_undecided(index, direction=1)
    group_html = _render_group(new_index)
    status = _status_text()
    nav = _nav_label(new_index)
    decision = _decision_label(new_index)
    return new_index, status, f"{nav} &mdash; {decision}", group_html


def on_execute():
    plan = _CACHE["plan"]
    groups = plan.get("groups", [])
    if not groups:
        return gr.update(visible=False), ""

    approved = [g for g in groups if g.get("decision") == "approved"]
    pending = sum(1 for g in groups if g.get("decision") is None)
    files_to_move = sum(len(g.get("move", [])) for g in approved)

    if not approved:
        return gr.update(visible=False), "**No approved groups to execute.**"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parent_name = Path(_CACHE["directory"]).parent.name
    msg = (
        f"**Move {files_to_move} files** from {len(approved)} approved groups "
        f"to `{parent_name}/_dupes_{timestamp}/`."
    )
    if pending:
        msg += f" {pending} pending groups will be skipped."

    return gr.update(visible=True), msg


def on_confirm():
    plan = _CACHE["plan"]
    directory = _CACHE["directory"]
    if not plan or not directory:
        return (
            gr.update(visible=False), "**Error:** No plan loaded.",
            "", "", placeholder_html("No plan loaded."),
        )

    directory = Path(directory)
    groups = plan.get("groups", [])
    approved = [g for g in groups if g.get("decision") == "approved"]

    if not approved:
        return (
            gr.update(visible=False), "**No approved groups.**",
            _status_text(), "Group -/-", placeholder_html("Nothing to execute."),
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dupes_dir = directory.parent / f"_dupes_{timestamp}"
    dupes_dir.mkdir(exist_ok=True)

    plan_path = directory / PLAN_FILENAME
    if plan_path.exists():
        shutil.copy2(plan_path, dupes_dir / PLAN_FILENAME)

    moved = 0
    errors = []
    for group in approved:
        for entry in group.get("move", []):
            json_name = entry["json"]
            json_path = directory / json_name

            if not json_path.exists():
                errors.append(f"{json_name}: not found")
                continue

            files_to_move = [json_path]

            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                companion = find_companion(json_path, data)
                if companion and companion.exists():
                    files_to_move.append(companion)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                pass

            stem = json_path.stem
            for extra_suffix in (".reconciliation.json",):
                extra = json_path.parent / (stem + extra_suffix)
                if extra.exists():
                    files_to_move.append(extra)

            for src in files_to_move:
                dst = dupes_dir / src.name
                if dst.exists():
                    base = dst.stem
                    suffix = dst.suffix
                    counter = 2
                    while dst.exists():
                        dst = dupes_dir / f"{base}_{counter}{suffix}"
                        counter += 1
                try:
                    src.rename(dst)
                    moved += 1
                except Exception as e:
                    errors.append(f"{src.name}: {e}")

    if plan_path.exists():
        plan_path.unlink()

    _CACHE["plan"] = {}
    _CACHE["directory"] = None

    error_msg = ""
    if errors:
        error_msg = f" Errors: {'; '.join(errors[:5])}"
        if len(errors) > 5:
            error_msg += f" (+{len(errors) - 5} more)"

    summary = (
        f"**Done.** Moved {moved} files to `{dupes_dir.name}/`.{error_msg} "
        f"Click Scan to start fresh."
    )

    return (
        gr.update(visible=False), summary,
        "No plan loaded.", "Group -/-",
        placeholder_html("Execution complete. Click Scan to start fresh."),
    )


def on_cancel():
    return gr.update(visible=False), ""

def build_ui():
    default_dir = get_processed_dir()

    with gr.Blocks(title="Papertrail Duplicate Review") as app:
        index_state = gr.State(0)

        gr.Markdown("## Papertrail Duplicate Review")

        with gr.Row():
            dir_input = gr.Textbox(
                label="Processed Directory",
                value=default_dir,
                scale=4,
            )
            scan_btn = gr.Button("Scan", scale=1)

        status_bar = gr.Markdown("No plan loaded.")

        with gr.Row(elem_id="approve_reject_row"):
            with gr.Column(elem_id="prev_btn", min_width=0, scale=1):
                prev_btn = gr.Button("< Prev", size="sm")
            with gr.Column(elem_id="approve_btn", min_width=0, scale=1):
                approve_btn = gr.Button(
                    "Approve (a)", variant="primary", size="sm",
                )
            with gr.Column(elem_id="reject_btn", min_width=0, scale=1):
                reject_btn = gr.Button(
                    "Reject (r)", variant="stop", size="sm",
                )
            with gr.Column(elem_id="next_btn", min_width=0, scale=1):
                next_btn = gr.Button("Next >", size="sm")

        nav_label = gr.Markdown("Group -/-")

        group_html = gr.HTML(
            placeholder_html("Enter a directory and click Scan.")
        )

        with gr.Row():
            execute_btn = gr.Button("Execute", size="sm", scale=1)
            exec_status = gr.Markdown("")

        confirm_row = gr.Row(visible=False)
        with confirm_row:
            gr.Markdown("")
            confirm_btn = gr.Button("Confirm", variant="primary", size="sm")
            cancel_btn = gr.Button("Cancel", size="sm")

        scan_btn.click(
            on_scan, [dir_input],
            [status_bar, nav_label, group_html, confirm_row],
        )

        prev_btn.click(
            on_prev, [index_state],
            [index_state, nav_label, group_html],
        )

        next_btn.click(
            on_next, [index_state],
            [index_state, nav_label, group_html],
        )

        approve_btn.click(
            on_approve, [index_state],
            [index_state, status_bar, nav_label, group_html],
        )

        reject_btn.click(
            on_reject, [index_state],
            [index_state, status_bar, nav_label, group_html],
        )

        execute_btn.click(
            on_execute, [],
            [confirm_row, exec_status],
        )

        confirm_btn.click(
            on_confirm, [],
            [confirm_row, exec_status, status_bar, nav_label, group_html],
        )

        cancel_btn.click(
            on_cancel, [],
            [confirm_row, exec_status],
        )

    return app

if __name__ == "__main__":
    build_ui().launch(css=FULLSCREEN_CSS + _CSS)
