---
name: gradio-dev
description: Autonomous visual dev loop for Gradio apps using Playwright MCP. Use when developing or debugging the review.py UI, or when asked to visually verify Gradio changes.
user-invocable: true
argument-hint: "[description of UI change to make]"
---

# Gradio Visual Dev Loop

Autonomous edit-screenshot-verify cycle for `review.py` using Playwright MCP browser tools and Gradio hot reload.

## Prerequisites

- Playwright MCP server configured in `.mcp.json`
- Gradio installed (`uv pip install gradio`)
- Browser installed (run `browser_install` tool if first time)

## Workflow

### 1. Start Gradio with hot reload

Check if port 7860 is already in use:

```bash
lsof -ti:7860
```

If not running, start in background:

```bash
cd /Users/tsilva/repos/tsilva/papertrail && gradio review.py
```

Wait 3-5s for server startup before proceeding.

### 2. Navigate and screenshot

1. `browser_navigate` to `http://127.0.0.1:7860`
2. `browser_take_screenshot` to capture current state
3. Read the screenshot to understand current UI

### 3. Edit-verify loop

For each change:

1. Edit `review.py` with the desired change
2. Wait ~2s for Gradio hot reload (the module-level `demo` variable enables this)
3. `browser_take_screenshot` to verify the change rendered correctly
4. Check `browser_console_messages` for JS errors
5. If issues found, fix and repeat from step 1

### 4. Interact with elements

When testing interactive behavior:

1. `browser_snapshot` to get the accessibility tree with element refs
2. `browser_click` on elements using refs from the snapshot
3. `browser_take_screenshot` to verify interaction result

## Rules

- Always screenshot AFTER edits, never assume the change worked
- Check console messages after each reload for errors
- If hot reload fails (blank page), the server may need a manual restart
- The `demo = build_ui()` line at module level in `review.py` is required for hot reload — do not remove it
- CSS is in `_CSS` and JS in `_JS` variables — they are applied via `demo.launch(css=_CSS, js=_JS)`
