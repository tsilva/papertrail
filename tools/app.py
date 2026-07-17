"""Unified Gradio app for browse, dedupe, and review workflows."""

import gradio as gr

from tools import browse, dedupe, review
from tools.shared import FULLSCREEN_CSS, FULLSCREEN_JS, launch_blocks

_CSS = "\n".join(
    [
        FULLSCREEN_CSS,
        browse._CSS,
        dedupe._CSS,
        review._CSS,
    ]
)

_JS = "\n".join(
    [
        FULLSCREEN_JS,
        browse._JS,
        dedupe._JS,
        review._JS,
    ]
)


def build_ui():
    """Build the unified tools UI."""
    with gr.Blocks(title="Papertrail Tools") as app:
        gr.Markdown("## Papertrail Tools")
        with gr.Tabs():
            with gr.Tab("Browse", id="browse"):
                browse.build_ui().render()
            with gr.Tab("Dedupe", id="dedupe"):
                dedupe.build_ui().render()
            with gr.Tab("Review", id="review"):
                review.build_ui().render()
    return app

if __name__ == "__main__":
    launch_blocks(build_ui(), css=_CSS, js=_JS)
