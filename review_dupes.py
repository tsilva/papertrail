"""Gradio duplicate review tool for auditing deduplication plans."""

import base64
import html as html_lib
import json
from datetime import datetime
from pathlib import Path

import fitz  # PyMuPDF
import gradio as gr

from papertrail.profiles import load_profile

# ── Constants ─────────────────────────────────────────────────────

PLAN_FILENAME = "_dupes_plan.json"
DECISIONS_FILENAME = "_dupes_decisions.json"

# ── Module-level cache (single-user local tool) ──────────────────

_CACHE = {
    "plan": {},
    "directory": None,
    "decisions": {},
}

# ── CSS ──────────────────────────────────────────────────────────

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
.preview-img {
    max-width: 100%; border: 1px solid var(--border-color-primary, #444);
    border-radius: 4px;
}
.placeholder {
    color: var(--body-text-color-subdued, #888); font-size: 14px;
    padding: 32px; text-align: center;
}
.status-approved { color: #4caf50; font-weight: 600; }
.status-rejected { color: #e57373; font-weight: 600; }
.status-pending { color: var(--body-text-color-subdued, #888); }
"""


# ── Helpers ──────────────────────────────────────────────────────

def _get_default_directory():
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


def _find_companion(json_path, data):
    """Find companion document file for a JSON sidecar."""
    ext = data.get("source_extension")
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
    """Render a single PDF page as a base64 PNG img tag."""
    try:
        with fitz.open(str(pdf_path)) as doc:
            total = len(doc)
            page_num = max(0, min(page_num, total - 1))
            pix = doc[page_num].get_pixmap(dpi=150)
            b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
            return f'<img src="data:image/png;base64,{b64}" class="preview-img"/>'
    except Exception:
        return '<p class="placeholder">Error rendering PDF.</p>'


def _render_file_card(entry, role, directory):
    """Render an HTML card for one file in a group.

    role is "KEEP" or "DUPE".
    """
    badge_class = "badge-keep" if role == "KEEP" else "badge-dupe"
    json_name = entry["json"]
    size_kb = entry.get("size_kb")
    hash_content = entry.get("hash_content", "?")
    size_str = f"{size_kb} KB" if size_kb is not None else "? KB"

    # Try to render PDF preview
    json_path = Path(directory) / json_name
    preview_html = ""
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            companion = _find_companion(json_path, data)
            if companion and companion.suffix.lower() == ".pdf":
                preview_html = _render_pdf_page_html(companion)
            elif companion and companion.suffix.lower() == ".xlsx":
                preview_html = (
                    '<p class="placeholder" style="padding:16px;">'
                    "XLSX file (no preview)</p>"
                )
            else:
                preview_html = (
                    '<p class="placeholder" style="padding:16px;">'
                    "No companion file</p>"
                )
        except Exception:
            preview_html = (
                '<p class="placeholder" style="padding:16px;">'
                "Error loading metadata</p>"
            )
    else:
        preview_html = (
            '<p class="placeholder" style="padding:16px;">'
            "JSON not found</p>"
        )

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
    """Render the full HTML for a duplicate group at the given index."""
    plan = _CACHE["plan"]
    directory = _CACHE["directory"]
    groups = plan.get("groups", [])

    if not groups or index < 0 or index >= len(groups):
        return '<p class="placeholder">No group to display.</p>'

    group = groups[index]
    cards = []

    # Keep card
    cards.append(_render_file_card(group["keep"], "KEEP", directory))

    # Dupe cards
    for entry in group.get("move", []):
        cards.append(_render_file_card(entry, "DUPE", directory))

    header = (
        f'<div style="margin-bottom:8px;font-size:13px;'
        f'color:var(--body-text-color-subdued,#aaa);">'
        f'Text hash: <code>{html_lib.escape(group.get("hash_text", "?"))}</code> '
        f'&middot; {1 + len(group.get("move", []))} files in group</div>'
    )

    return header + '<div class="group-container">' + "".join(cards) + "</div>"


def _status_text():
    """Build status bar markdown from current state."""
    groups = _CACHE["plan"].get("groups", [])
    total = len(groups)
    if total == 0:
        return "No plan loaded."

    decisions = _CACHE["decisions"]
    approved = sum(1 for v in decisions.values() if v == "approved")
    rejected = sum(1 for v in decisions.values() if v == "rejected")
    pending = total - approved - rejected

    return (
        f"**{total}** groups &mdash; "
        f'<span class="status-approved">{approved} approved</span>, '
        f'<span class="status-rejected">{rejected} rejected</span>, '
        f'<span class="status-pending">{pending} pending</span>'
    )


def _decision_label(index):
    """Return the decision label for a given group index."""
    d = _CACHE["decisions"].get(index)
    if d == "approved":
        return '<span class="status-approved">APPROVED</span>'
    elif d == "rejected":
        return '<span class="status-rejected">REJECTED</span>'
    return '<span class="status-pending">PENDING</span>'


def _nav_label(index):
    """Return navigation label like 'Group 3/15'."""
    total = len(_CACHE["plan"].get("groups", []))
    if total == 0:
        return "Group -/-"
    return f"Group {index + 1}/{total}"


def _next_undecided(start, direction=1):
    """Find the next undecided group index from start in the given direction."""
    groups = _CACHE["plan"].get("groups", [])
    total = len(groups)
    if total == 0:
        return start

    idx = start + direction
    while 0 <= idx < total:
        if idx not in _CACHE["decisions"]:
            return idx
        idx += direction

    # No undecided found, clamp to bounds
    return max(0, min(start + direction, total - 1))


# ── Event handlers ───────────────────────────────────────────────

def on_load(directory_path):
    """Load a deduplication plan from the given directory."""
    directory_path = directory_path.strip().strip("'\"")
    directory = Path(directory_path)

    if not directory.is_dir():
        _CACHE["plan"] = {}
        _CACHE["directory"] = None
        _CACHE["decisions"] = {}
        return (
            f"**Error:** `{directory_path}` is not a valid directory.",
            "", '<p class="placeholder">No plan loaded.</p>',
        )

    plan_path = directory / PLAN_FILENAME
    if not plan_path.exists():
        _CACHE["plan"] = {}
        _CACHE["directory"] = None
        _CACHE["decisions"] = {}
        return (
            f"**Error:** No `{PLAN_FILENAME}` found in `{directory_path}`.",
            "", '<p class="placeholder">No plan found. Run deduplicate.py plan first.</p>',
        )

    with open(plan_path, "r", encoding="utf-8") as f:
        plan_data = json.load(f)

    _CACHE["plan"] = plan_data
    _CACHE["directory"] = str(directory)
    _CACHE["decisions"] = {}

    # Load existing decisions if present
    decisions_path = directory / DECISIONS_FILENAME
    if decisions_path.exists():
        try:
            with open(decisions_path, "r", encoding="utf-8") as f:
                decisions_data = json.load(f)
            # Validate plan_generated matches
            if decisions_data.get("plan_generated") == plan_data.get("generated"):
                for k, v in decisions_data.get("decisions", {}).items():
                    _CACHE["decisions"][int(k)] = v
        except Exception:
            pass

    # Render group 0
    group_html = _render_group(0)
    status = _status_text()
    nav = _nav_label(0)
    decision = _decision_label(0)

    return status, f"{nav} &mdash; {decision}", group_html


def on_prev(index):
    """Navigate to previous group."""
    new_index = max(0, index - 1)
    group_html = _render_group(new_index)
    nav = _nav_label(new_index)
    decision = _decision_label(new_index)
    return new_index, f"{nav} &mdash; {decision}", group_html


def on_next(index):
    """Navigate to next group."""
    total = len(_CACHE["plan"].get("groups", []))
    new_index = min(total - 1, index + 1) if total > 0 else 0
    group_html = _render_group(new_index)
    nav = _nav_label(new_index)
    decision = _decision_label(new_index)
    return new_index, f"{nav} &mdash; {decision}", group_html


def on_approve(index):
    """Approve current group and advance to next undecided."""
    _CACHE["decisions"][index] = "approved"
    new_index = _next_undecided(index, direction=1)
    group_html = _render_group(new_index)
    status = _status_text()
    nav = _nav_label(new_index)
    decision = _decision_label(new_index)
    return new_index, status, f"{nav} &mdash; {decision}", group_html


def on_reject(index):
    """Reject current group and advance to next undecided."""
    _CACHE["decisions"][index] = "rejected"
    new_index = _next_undecided(index, direction=1)
    group_html = _render_group(new_index)
    status = _status_text()
    nav = _nav_label(new_index)
    decision = _decision_label(new_index)
    return new_index, status, f"{nav} &mdash; {decision}", group_html


def on_save():
    """Save decisions to JSON file alongside the plan."""
    directory = _CACHE["directory"]
    plan = _CACHE["plan"]
    decisions = _CACHE["decisions"]

    if not directory or not plan:
        return "**Error:** No plan loaded."

    groups = plan.get("groups", [])
    total = len(groups)
    approved = sum(1 for v in decisions.values() if v == "approved")
    rejected = sum(1 for v in decisions.values() if v == "rejected")
    pending = total - approved - rejected

    decisions_data = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "plan_generated": plan.get("generated"),
        "directory": directory,
        "decisions": {str(k): v for k, v in sorted(decisions.items())},
        "summary": {
            "total_groups": total,
            "approved": approved,
            "rejected": rejected,
            "pending": pending,
        },
    }

    out_path = Path(directory) / DECISIONS_FILENAME
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(decisions_data, f, indent=4, ensure_ascii=False)

    return f"Saved to `{out_path.name}` — {approved} approved, {rejected} rejected, {pending} pending"


# ── Gradio UI ────────────────────────────────────────────────────

def build_ui():
    default_dir = _get_default_directory()

    with gr.Blocks(title="Papertrail Duplicate Review", css=_CSS) as app:
        index_state = gr.State(0)

        gr.Markdown("## Papertrail Duplicate Review")

        with gr.Row():
            dir_input = gr.Textbox(
                label="Processed Directory",
                value=default_dir,
                scale=4,
            )
            load_btn = gr.Button("Load", scale=1)

        status_bar = gr.Markdown("No plan loaded.")

        with gr.Row():
            prev_btn = gr.Button("< Prev", size="sm", scale=1)
            approve_btn = gr.Button(
                "Approve", variant="primary", size="sm", scale=1,
            )
            reject_btn = gr.Button(
                "Reject", variant="stop", size="sm", scale=1,
            )
            next_btn = gr.Button("Next >", size="sm", scale=1)

        nav_label = gr.Markdown("Group -/-")

        group_html = gr.HTML(
            '<p class="placeholder">Load a directory with a deduplication plan.</p>'
        )

        with gr.Row():
            save_btn = gr.Button("Save Decisions", size="sm", scale=1)
            save_status = gr.Markdown("")

        # ── Wire events ──────────────────────────────────────────

        load_btn.click(
            on_load, [dir_input],
            [status_bar, nav_label, group_html],
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

        save_btn.click(on_save, [], [save_status])

    return app


demo = build_ui()

if __name__ == "__main__":
    demo.launch()
