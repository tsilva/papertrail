#!/usr/bin/env python3
"""Gradio PDF Compression Review Tool.

A web UI for comparing original and compressed PDF pairs side-by-side,
allowing users to accept or skip each compression.
"""

import argparse
import io
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
import gradio as gr
from PIL import Image


@dataclass
class ComparisonState:
    """Track the state of the PDF comparison session."""

    folder_path: Path | None = None
    pairs: list[tuple[Path, Path]] = field(default_factory=list)
    current_index: int = 0
    accepted_count: int = 0
    skipped_count: int = 0


# Global state instance
state = ComparisonState()


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.2f} MB"


def scan_for_pdf_pairs(folder_path: Path) -> list[tuple[Path, Path]]:
    """Find all (original.pdf, original.compressed.pdf) pairs."""
    pairs = []
    compressed_files = list(folder_path.glob("*.compressed.pdf"))

    for compressed in compressed_files:
        # Get original filename by removing .compressed suffix
        original_name = compressed.name.replace(".compressed.pdf", ".pdf")
        original = compressed.parent / original_name

        if original.exists():
            pairs.append((original, compressed))

    # Sort by filename for consistent ordering
    return sorted(pairs, key=lambda p: p[0].name.lower())


def render_pdf_preview(pdf_path: Path, max_width: int = 500) -> Image.Image | None:
    """Render the first page of a PDF as a PIL Image."""
    try:
        with fitz.open(str(pdf_path)) as doc:
            if len(doc) == 0:
                return None

            page = doc[0]
            # Calculate zoom to fit max_width
            zoom = max_width / page.rect.width
            matrix = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=matrix)

            # Convert to PIL Image
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            return img
    except Exception as e:
        print(f"Error rendering PDF {pdf_path}: {e}")
        return None


def get_pdf_info(pdf_path: Path) -> dict:
    """Get metadata about a PDF file."""
    try:
        size = pdf_path.stat().st_size
        with fitz.open(str(pdf_path)) as doc:
            page_count = len(doc)

        return {
            "name": pdf_path.name,
            "size": size,
            "size_formatted": format_file_size(size),
            "page_count": page_count,
        }
    except Exception as e:
        return {
            "name": pdf_path.name,
            "size": 0,
            "size_formatted": "Error",
            "page_count": 0,
            "error": str(e),
        }


def accept_compression(original: Path, compressed: Path) -> tuple[bool, str]:
    """
    Accept the compression by renaming files.

    1. Rename original.pdf -> original.uncompressed.pdf
    2. Rename original.compressed.pdf -> original.pdf
    """
    uncompressed = original.with_suffix(".uncompressed.pdf")

    # Safety check: don't overwrite existing uncompressed file
    if uncompressed.exists():
        return False, f"Cannot accept: {uncompressed.name} already exists"

    try:
        # Step 1: Rename original to uncompressed
        original.rename(uncompressed)

        # Step 2: Rename compressed to original
        compressed.rename(original)

        return True, f"Accepted: {original.name}"
    except Exception as e:
        return False, f"Error during rename: {e}"


def calculate_size_reduction(original_size: int, compressed_size: int) -> str:
    """Calculate the percentage size reduction."""
    if original_size == 0:
        return "N/A"

    reduction = ((original_size - compressed_size) / original_size) * 100
    if reduction > 0:
        return f"{reduction:.1f}% smaller"
    elif reduction < 0:
        return f"{abs(reduction):.1f}% larger"
    else:
        return "Same size"


def get_current_pair_display():
    """Get display data for the current pair."""
    if not state.pairs or state.current_index >= len(state.pairs):
        return (
            None,  # original preview
            None,  # compressed preview
            "No pairs to review",  # original info
            "",  # compressed info
            "",  # size reduction
            "",  # page warning
            "No PDF pairs found in folder",  # progress
            "",  # status
        )

    original, compressed = state.pairs[state.current_index]

    # Get info
    orig_info = get_pdf_info(original)
    comp_info = get_pdf_info(compressed)

    # Render previews
    orig_preview = render_pdf_preview(original)
    comp_preview = render_pdf_preview(compressed)

    # Format info strings
    orig_info_str = f"**{orig_info['name']}**\n\nSize: {orig_info['size_formatted']}\n\nPages: {orig_info['page_count']}"
    comp_info_str = f"**{comp_info['name']}**\n\nSize: {comp_info['size_formatted']}\n\nPages: {comp_info['page_count']}"

    # Size reduction
    reduction = calculate_size_reduction(orig_info["size"], comp_info["size"])

    # Page count warning
    page_warning = ""
    if orig_info["page_count"] != comp_info["page_count"]:
        page_warning = f"**PAGE COUNT MISMATCH: Original has {orig_info['page_count']} pages, compressed has {comp_info['page_count']} pages**"

    # Progress
    progress = f"Reviewing {state.current_index + 1} of {len(state.pairs)} pairs"
    if state.accepted_count > 0 or state.skipped_count > 0:
        progress += f" (Accepted: {state.accepted_count}, Skipped: {state.skipped_count})"

    return (
        orig_preview,
        comp_preview,
        orig_info_str,
        comp_info_str,
        f"**Size Reduction: {reduction}**",
        page_warning,
        progress,
        "Ready",
    )


def refresh_handler(folder_path: str):
    """Handle refresh button click - rescan folder."""
    path = Path(folder_path)

    if not path.exists():
        return (
            None,
            None,
            "Folder not found",
            "",
            "",
            "",  # page warning
            f"Error: Folder does not exist: {folder_path}",
            "Error",
        )

    if not path.is_dir():
        return (
            None,
            None,
            "Not a folder",
            "",
            "",
            "",  # page warning
            f"Error: Path is not a folder: {folder_path}",
            "Error",
        )

    # Update state
    state.folder_path = path
    state.pairs = scan_for_pdf_pairs(path)
    state.current_index = 0
    state.accepted_count = 0
    state.skipped_count = 0

    if not state.pairs:
        return (
            None,
            None,
            "No pairs found",
            "",
            "",
            "",  # page warning
            f"No PDF pairs found in {folder_path}",
            "Scan complete - no pairs",
        )

    return get_current_pair_display()


def accept_handler():
    """Handle accept button click."""
    if not state.pairs or state.current_index >= len(state.pairs):
        return get_current_pair_display()

    original, compressed = state.pairs[state.current_index]
    success, message = accept_compression(original, compressed)

    if success:
        state.accepted_count += 1
        # Remove this pair from the list since it's been processed
        state.pairs.pop(state.current_index)
        # Don't increment index since we removed the current item
        if state.current_index >= len(state.pairs):
            state.current_index = max(0, len(state.pairs) - 1)
    else:
        # On failure, show error but don't advance
        result = list(get_current_pair_display())
        result[-1] = f"Error: {message}"
        return tuple(result)

    # Check if we're done
    if not state.pairs:
        return (
            None,
            None,
            "All done!",
            "",
            "",
            "",  # page warning
            f"Completed! Accepted: {state.accepted_count}, Skipped: {state.skipped_count}",
            message,
        )

    result = list(get_current_pair_display())
    result[-1] = message
    return tuple(result)


def skip_handler():
    """Handle skip button click."""
    if not state.pairs or state.current_index >= len(state.pairs):
        return get_current_pair_display()

    state.skipped_count += 1
    state.current_index += 1

    # Wrap around if at end
    if state.current_index >= len(state.pairs):
        state.current_index = 0

        # If we've gone through all, show completion
        if state.skipped_count >= len(state.pairs):
            return (
                None,
                None,
                "All pairs reviewed",
                "",
                "",
                "",  # page warning
                f"Completed! Accepted: {state.accepted_count}, Skipped: {state.skipped_count}",
                "Skipped all remaining pairs",
            )

    result = list(get_current_pair_display())
    result[-1] = "Skipped"
    return tuple(result)


# CSS for the page warning
CUSTOM_CSS = """
.page-warning {
    background-color: #fee2e2 !important;
    border: 2px solid #dc2626 !important;
    border-radius: 8px !important;
    padding: 12px !important;
    text-align: center !important;
}
.page-warning p {
    color: #dc2626 !important;
    margin: 0 !important;
    font-size: 1.1em !important;
}
"""

# JavaScript for keyboard shortcuts
KEYBOARD_JS = """
function setupKeyboardShortcuts() {
    document.addEventListener('keydown', function(e) {
        // Ignore if typing in an input field
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') {
            return;
        }

        // Enter or 'a' for Accept
        if (e.key === 'Enter' || e.key === 'a' || e.key === 'A') {
            e.preventDefault();
            const acceptBtn = document.getElementById('accept-btn');
            if (acceptBtn) acceptBtn.click();
        }
        // Escape or 's' for Skip
        else if (e.key === 'Escape' || e.key === 's' || e.key === 'S') {
            e.preventDefault();
            const skipBtn = document.getElementById('skip-btn');
            if (skipBtn) skipBtn.click();
        }
        // 'r' for Refresh
        else if (e.key === 'r' || e.key === 'R') {
            e.preventDefault();
            const refreshBtn = document.getElementById('refresh-btn');
            if (refreshBtn) refreshBtn.click();
        }
    });
    return 'Keyboard shortcuts initialized';
}
"""


def build_interface(default_folder: str | None = None) -> gr.Blocks:
    """Build and return the Gradio interface."""
    with gr.Blocks(title="PDF Compression Review Tool") as demo:
        gr.Markdown("# PDF Compression Review Tool")
        gr.Markdown("Compare original and compressed PDFs side-by-side. **Keyboard shortcuts:** `Enter`/`A` = Accept, `Esc`/`S` = Skip, `R` = Refresh")

        with gr.Row():
            folder_input = gr.Textbox(
                label="Folder Path",
                placeholder="/path/to/folder",
                value=default_folder or "",
                scale=4,
            )
            refresh_btn = gr.Button("Refresh", scale=1, elem_id="refresh-btn")

        progress_text = gr.Markdown("Click Refresh to scan folder")

        # Page count warning (prominent, only visible when there's a mismatch)
        page_warning = gr.Markdown("", elem_classes=["page-warning"], visible=True)

        with gr.Row():
            with gr.Column():
                gr.Markdown("### Original")
                orig_preview = gr.Image(label="Original Preview", type="pil")
                orig_info = gr.Markdown("No file loaded")

            with gr.Column():
                gr.Markdown("### Compressed")
                comp_preview = gr.Image(label="Compressed Preview", type="pil")
                comp_info = gr.Markdown("No file loaded")

        size_reduction = gr.Markdown("")

        with gr.Row():
            skip_btn = gr.Button("Skip (S)", variant="secondary", scale=1, elem_id="skip-btn")
            accept_btn = gr.Button("Accept (Enter)", variant="primary", scale=1, elem_id="accept-btn")

        status_text = gr.Markdown("Ready")

        # Wire up event handlers
        outputs = [
            orig_preview,
            comp_preview,
            orig_info,
            comp_info,
            size_reduction,
            page_warning,
            progress_text,
            status_text,
        ]

        refresh_btn.click(refresh_handler, inputs=[folder_input], outputs=outputs)
        accept_btn.click(accept_handler, inputs=[], outputs=outputs)
        skip_btn.click(skip_handler, inputs=[], outputs=outputs)

    return demo


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(description="PDF Compression Review Tool")
    parser.add_argument(
        "--folder",
        type=str,
        default=None,
        help="Default folder path to scan",
    )
    parser.add_argument(
        "--share",
        action="store_true",
        help="Create a public share link",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=7860,
        help="Port to run the server on (default: 7860)",
    )

    args = parser.parse_args()

    demo = build_interface(default_folder=args.folder)
    demo.launch(share=args.share, server_port=args.port, css=CUSTOM_CSS, js=KEYBOARD_JS)


if __name__ == "__main__":
    main()
