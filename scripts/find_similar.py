#!/usr/bin/env python3
"""
Find near-duplicate documents via visual embeddings.

Uses Cohere Embed v4 API to generate per-page image embeddings and find
visually similar documents that differ at the pixel level (e.g., monthly
invoices from the same vendor, rescanned copies).

Two-tier comparison:
  1. Document-level: mean of page embeddings → fast all-pairs cosine similarity
  2. Page-level: detailed per-page comparison for candidate pairs

Embeddings are cached as .embeddings.json sidecars next to each PDF.

Requires COHERE_API_KEY environment variable (free trial at cohere.com/dashboard).

Usage:
  python scripts/find_similar.py <processed_path> [options]
  python scripts/find_similar.py /path/to/docs --threshold 0.90 -v
"""

import argparse
import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import cohere
import fitz  # PyMuPDF
import numpy as np
from PIL import Image

from papertrail.console import get_console
from papertrail.hashing import hash_file_fast
from papertrail.metadata import find_companion_file, iter_json_files

_SIDECAR_VERSION = 3  # Cohere embed-v4.0 remote API


# ---------------------------------------------------------------------------
# Stage 1 — Discover documents
# ---------------------------------------------------------------------------

def discover_documents(directory: Path) -> list[dict]:
    """Find PDFs with metadata in *directory*."""
    docs = []
    for json_path, data in iter_json_files(directory):
        pdf_path = find_companion_file(json_path, data)
        if pdf_path is None or not pdf_path.exists():
            continue
        if pdf_path.suffix.lower() != ".pdf":
            continue
        docs.append({
            "json_path": json_path,
            "pdf_path": pdf_path,
            "metadata": data,
            "hash_file": hash_file_fast(pdf_path),
        })
    return docs


# ---------------------------------------------------------------------------
# Stage 2 — Embeddings with sidecar cache
# ---------------------------------------------------------------------------

def render_pdf_pages(pdf_path: Path, dpi: int = 150, max_pages: int | None = None) -> list[Image.Image]:
    """Render PDF pages to PIL Images via PyMuPDF."""
    images = []
    zoom = dpi / 72
    mat = fitz.Matrix(zoom, zoom)
    with fitz.open(str(pdf_path)) as doc:
        n = len(doc) if max_pages is None else min(max_pages, len(doc))
        for i in range(n):
            pix = doc[i].get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            images.append(img)
    return images


def _image_to_base64_url(img: Image.Image, quality: int = 85) -> str:
    """Convert PIL Image to base64 data URL for Cohere API."""
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _sidecar_path(pdf_path: Path) -> Path:
    return pdf_path.with_suffix(".embeddings.json")


def load_embeddings(pdf_path: Path, expected_hash: str, dim: int) -> list[list[float]] | None:
    """Load cached embeddings if sidecar is valid."""
    path = _sidecar_path(pdf_path)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if (
            data.get("hash_file") == expected_hash
            and data.get("dim") == dim
            and data.get("version") == _SIDECAR_VERSION
        ):
            return data["embeddings"]
    except Exception:
        pass
    return None


def save_embeddings(
    pdf_path: Path, hash_file: str, dim: int, embeddings: list[list[float]]
) -> None:
    """Write .embeddings.json sidecar."""
    path = _sidecar_path(pdf_path)
    data = {
        "dim": dim,
        "embeddings": embeddings,
        "hash_file": hash_file,
        "version": _SIDECAR_VERSION,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


def encode_images_api(
    client: cohere.ClientV2,
    images: list[Image.Image],
    dim: int,
    batch_size: int,
) -> np.ndarray:
    """Encode PIL Images → (N, dim) numpy array via Cohere Embed v4 API."""
    all_embs = []
    for i in range(0, len(images), batch_size):
        batch = images[i : i + batch_size]
        inputs = [
            {"content": [{"type": "image_url", "image_url": {"url": _image_to_base64_url(img)}}]}
            for img in batch
        ]

        for attempt in range(3):
            try:
                resp = client.embed(
                    model="embed-v4.0",
                    inputs=inputs,
                    input_type="search_document",
                    embedding_types=["float"],
                )
                break
            except cohere.TooManyRequestsError:
                wait = 2 ** (attempt + 1)
                time.sleep(wait)
        else:
            # Final attempt — let it raise
            resp = client.embed(
                model="embed-v4.0",
                inputs=inputs,
                input_type="search_document",
                embedding_types=["float"],
            )

        batch_embs = np.array(resp.embeddings.float_, dtype=np.float32)
        # Truncate to requested dim (Cohere returns 1536 by default)
        if batch_embs.shape[1] > dim:
            batch_embs = batch_embs[:, :dim]
            # Re-normalize after truncation (Matryoshka-style)
            norms = np.linalg.norm(batch_embs, axis=1, keepdims=True)
            batch_embs = batch_embs / (norms + 1e-8)
        all_embs.append(batch_embs)

    return np.vstack(all_embs) if all_embs else np.empty((0, dim), dtype=np.float32)


def compute_all_embeddings(
    docs: list[dict],
    client: cohere.ClientV2,
    dim: int,
    batch_size: int,
    dpi: int,
    max_pages: int | None,
    verbose: bool,
) -> list[np.ndarray]:
    """Compute or load cached embeddings for every document."""
    console = get_console()
    page_embeddings: list[np.ndarray] = []
    cache_hits = 0
    cache_misses = 0

    for doc in console.track(docs, "Computing embeddings"):
        pdf_path = doc["pdf_path"]
        file_hash = doc["hash_file"]

        cached = load_embeddings(pdf_path, file_hash, dim)
        if cached is not None:
            page_embeddings.append(np.array(cached, dtype=np.float32))
            cache_hits += 1
            continue

        cache_misses += 1
        images = render_pdf_pages(pdf_path, dpi=dpi, max_pages=max_pages)
        if not images:
            page_embeddings.append(np.empty((0, dim), dtype=np.float32))
            continue

        embs = encode_images_api(client, images, dim, batch_size)
        page_embeddings.append(embs)
        save_embeddings(pdf_path, file_hash, dim, embs.tolist())
        del images

    console.info(
        f"Embeddings: [green]{cache_hits}[/green] cached, "
        f"[yellow]{cache_misses}[/yellow] computed via API",
        indent=False,
    )
    return page_embeddings


# ---------------------------------------------------------------------------
# Stage 3 — Document-level fast comparison
# ---------------------------------------------------------------------------

def compute_document_embeddings(page_embs_list: list[np.ndarray]) -> np.ndarray:
    """Compute L2-normalized mean of page embeddings per document."""
    dim = page_embs_list[0].shape[1] if page_embs_list and page_embs_list[0].shape[0] > 0 else 512
    doc_embs = []
    for embs in page_embs_list:
        if embs.shape[0] == 0:
            doc_embs.append(np.zeros(dim, dtype=np.float32))
        else:
            mean = embs.mean(axis=0)
            norm = np.linalg.norm(mean)
            if norm > 0:
                mean /= norm
            doc_embs.append(mean)
    return np.vstack(doc_embs)


def find_candidate_pairs(
    doc_embs: np.ndarray,
    docs: list[dict],
    candidate_threshold: float,
) -> list[tuple[int, int, float]]:
    """All-pairs cosine similarity, return pairs above threshold."""
    sim_matrix = doc_embs @ doc_embs.T
    pairs = []
    n = len(docs)
    for i in range(n):
        for j in range(i + 1, n):
            score = float(sim_matrix[i, j])
            if score < candidate_threshold:
                continue
            h_i = docs[i]["metadata"].get("hash_content")
            h_j = docs[j]["metadata"].get("hash_content")
            if h_i and h_j and h_i == h_j:
                continue
            pairs.append((i, j, score))
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs


# ---------------------------------------------------------------------------
# Stage 4 — Page-level refinement
# ---------------------------------------------------------------------------

def page_level_similarity(emb_a: np.ndarray, emb_b: np.ndarray) -> float:
    """Compute page-level similarity between two documents."""
    if emb_a.shape[0] == 0 or emb_b.shape[0] == 0:
        return 0.0
    norm_a = emb_a / (np.linalg.norm(emb_a, axis=1, keepdims=True) + 1e-8)
    norm_b = emb_b / (np.linalg.norm(emb_b, axis=1, keepdims=True) + 1e-8)
    cross_sim = norm_a @ norm_b.T
    if emb_a.shape[0] == emb_b.shape[0]:
        return float(np.diag(cross_sim).mean())
    else:
        if emb_a.shape[0] > emb_b.shape[0]:
            cross_sim = cross_sim.T
        return float(cross_sim.max(axis=1).mean())


def refine_candidates(
    candidates: list[tuple[int, int, float]],
    page_embs_list: list[np.ndarray],
    threshold: float,
) -> list[dict]:
    """Refine candidate pairs with page-level comparison."""
    results = []
    for i, j, doc_sim in candidates:
        page_sim = page_level_similarity(page_embs_list[i], page_embs_list[j])
        if page_sim >= threshold:
            results.append({
                "i": i,
                "j": j,
                "doc_similarity": doc_sim,
                "page_similarity": page_sim,
            })
    results.sort(key=lambda x: x["page_similarity"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Stage 5 — Output
# ---------------------------------------------------------------------------

def _short_name(path: Path) -> str:
    return path.stem


def display_results(results: list[dict], docs: list[dict]) -> None:
    console = get_console()
    if not results:
        console.info("[green]No near-duplicate pairs found.[/green]", indent=False)
        return

    console.console.print()
    console.console.print(f"[bold]Found {len(results)} near-duplicate pair(s):[/bold]")
    console.console.print()

    for rank, r in enumerate(results, 1):
        i, j = r["i"], r["j"]
        page_sim = r["page_similarity"]
        doc_sim = r["doc_similarity"]

        if page_sim >= 0.95:
            color = "red"
        elif page_sim >= 0.90:
            color = "yellow"
        else:
            color = "cyan"

        name_a = _short_name(docs[i]["pdf_path"])
        name_b = _short_name(docs[j]["pdf_path"])
        pages_a = docs[i].get("page_count", "?")
        pages_b = docs[j].get("page_count", "?")

        console.console.print(
            f"  [{color}]{rank}.[/] "
            f"[{color} bold]{page_sim:.1%}[/] page  "
            f"[dim]({doc_sim:.1%} doc)[/dim]"
        )
        console.console.print(f"     [dim]A:[/dim] {name_a}  [dim]({pages_a}p)[/dim]")
        console.console.print(f"     [dim]B:[/dim] {name_b}  [dim]({pages_b}p)[/dim]")
        console.console.print()


def write_output_json(results: list[dict], docs: list[dict], output_path: Path) -> None:
    output = []
    for r in results:
        i, j = r["i"], r["j"]
        output.append({
            "page_similarity": round(r["page_similarity"], 4),
            "doc_similarity": round(r["doc_similarity"], 4),
            "file_a": str(docs[i]["pdf_path"]),
            "file_b": str(docs[j]["pdf_path"]),
            "hash_a": docs[i]["hash_file"],
            "hash_b": docs[j]["hash_file"],
        })
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Find near-duplicate documents via visual embeddings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("processed_path", type=str, help="Directory with processed PDFs + JSON sidecars")
    parser.add_argument("--threshold", type=float, default=0.95, help="Page-level similarity threshold (default: 0.95)")
    parser.add_argument("--candidate-threshold", type=float, default=0.85, help="Doc-level candidate threshold (default: 0.85)")
    parser.add_argument("--dim", type=int, default=512, help="Embedding dimension (default: 512)")
    parser.add_argument("--batch-size", type=int, default=8, help="Images per API call (default: 8)")
    parser.add_argument("--dpi", type=int, default=150, help="PDF render resolution (default: 150)")
    parser.add_argument("--max-pages", type=int, default=None, help="Max pages per document (default: all)")
    parser.add_argument("--output", type=str, default=None, help="Write results JSON to this path")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    args = parser.parse_args()

    console = get_console()
    processed_path = Path(args.processed_path)

    if not processed_path.is_dir():
        console.error(f"Not a directory: {processed_path}", indent=False)
        sys.exit(1)

    api_key = os.environ.get("COHERE_API_KEY") or os.environ.get("CO_API_KEY")
    if not api_key:
        console.error("COHERE_API_KEY environment variable not set", indent=False)
        console.error("Get a free key at https://dashboard.cohere.com/api-keys", indent=False)
        sys.exit(1)

    client = cohere.ClientV2(api_key=api_key)

    t_start = time.monotonic()

    # --- Stage 1: Discover ---
    console.info("[bold]Stage 1:[/bold] Discovering documents...", indent=False)
    docs = discover_documents(processed_path)
    if not docs:
        console.error("No PDF documents found.", indent=False)
        sys.exit(1)

    for doc in docs:
        pc = doc["metadata"].get("page_count")
        if pc:
            doc["page_count"] = pc

    console.info(f"Found [cyan]{len(docs)}[/cyan] PDF documents", indent=False)

    # --- Stage 2: Embeddings ---
    console.console.print()
    console.info("[bold]Stage 2:[/bold] Loading/computing embeddings...", indent=False)

    need_compute = sum(
        1 for doc in docs
        if load_embeddings(doc["pdf_path"], doc["hash_file"], args.dim) is None
    )

    if need_compute > 0:
        console.info(f"{need_compute} document(s) need embedding via Cohere API", indent=False)
    else:
        console.info("All embeddings cached", indent=False)

    page_embs_list = compute_all_embeddings(
        docs, client, args.dim, args.batch_size, args.dpi, args.max_pages, args.verbose,
    )

    # --- Stage 3: Document-level comparison ---
    console.console.print()
    console.info("[bold]Stage 3:[/bold] Document-level comparison...", indent=False)
    doc_embs = compute_document_embeddings(page_embs_list)
    candidates = find_candidate_pairs(doc_embs, docs, args.candidate_threshold)
    console.info(
        f"Found [cyan]{len(candidates)}[/cyan] candidate pairs "
        f"(threshold: {args.candidate_threshold})",
        indent=False,
    )

    # --- Stage 4: Page-level refinement ---
    if candidates:
        console.console.print()
        console.info("[bold]Stage 4:[/bold] Page-level refinement...", indent=False)
        results = refine_candidates(candidates, page_embs_list, args.threshold)
        console.info(
            f"[cyan]{len(results)}[/cyan] pairs above page threshold "
            f"({args.threshold})",
            indent=False,
        )
    else:
        results = []

    # --- Stage 5: Output ---
    display_results(results, docs)

    if args.output:
        output_path = Path(args.output)
        write_output_json(results, docs, output_path)
        console.info(f"Results written to [dim]{output_path}[/dim]", indent=False)

    elapsed = time.monotonic() - t_start
    console.console.print()
    console.info(f"[dim]Completed in {elapsed:.1f}s[/dim]", indent=False)


if __name__ == "__main__":
    main()
