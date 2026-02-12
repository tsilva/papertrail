#!/usr/bin/env python3
"""Deduplicate files by content/text hash — plan then execute.

Usage:
    python scripts/deduplicate.py plan <directory>
    python scripts/deduplicate.py execute <directory> [--dry-run]

Plan phase:
  - Scans all JSON sidecars in <directory>
  - For PDFs without hash_text, computes it on-the-fly (does NOT write back)
  - Groups files by hash_content (primary) or hash_text (fallback)
  - Keeps the smallest file per group (most compressed)
  - Writes _dupes_plan.json to <directory>

Execute phase:
  - Reads _dupes_plan.json from <directory>
  - Moves duplicate files (JSON + companion + any extra sidecars) to _dupes/ subfolder
  - Respects embedded decisions if present (approved/rejected/pending)
  - Use --dry-run to preview without moving
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


PLAN_FILENAME = "_dupes_plan.json"
DUPES_DIRNAME = "_dupes"


def _find_companion(json_path: Path, data: dict) -> Path | None:
    """Find the companion document file for a JSON sidecar."""
    ext = data.get("source_extension")
    if ext:
        candidate = json_path.with_suffix(ext)
        if candidate.exists():
            return candidate

    for fallback_ext in (".pdf", ".xlsx"):
        candidate = json_path.with_suffix(fallback_ext)
        if candidate.exists():
            return candidate

    return None


def _get_text_hash(json_path: Path, data: dict) -> str | None:
    """Get text hash from metadata, or compute on-the-fly for PDFs."""
    text_hash = data.get("hash_text")
    if text_hash:
        return text_hash

    # Try to compute for PDFs
    companion = _find_companion(json_path, data)
    if companion is None or companion.suffix.lower() != ".pdf":
        return None

    from papertrail.hashing import hash_file_text
    return hash_file_text(companion)


def _get_file_size(json_path: Path, data: dict) -> int | None:
    """Get file size from metadata or compute from companion."""
    size = data.get("file_size_kb")
    if size is not None:
        return size

    companion = _find_companion(json_path, data)
    if companion and companion.exists():
        return round(companion.stat().st_size / 1024)

    return None


def scan_directory(directory: Path) -> dict:
    """Scan directory and return deduplication plan dict (does not write to disk).

    Returns a plan dict with groups, summary, and scan_stats.
    Each group has a `decision` field (initially None).
    Groups use hash_content (primary) or hash_text (fallback).
    """
    from papertrail.dedup import group_duplicates

    json_files = sorted(directory.rglob("*.json"))

    file_records: list[dict] = []
    scanned = 0
    skipped_no_hash = 0

    for json_path in json_files:
        # Skip logs, internal files, sidecars, and _dupes* folders
        if "/logs/" in str(json_path) or json_path.name.startswith("_"):
            continue
        if json_path.name.endswith(".reconciliation.json"):
            continue
        if json_path.name.endswith(".embeddings.json"):
            continue
        if any(part.startswith("_dupes") for part in json_path.parts):
            continue

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        scanned += 1

        content_hash = data.get("hash_content")
        text_hash = _get_text_hash(json_path, data)

        if not content_hash and not text_hash:
            skipped_no_hash += 1
            continue

        file_records.append({
            "json": json_path.name,
            "json_path": str(json_path),
            "size_kb": _get_file_size(json_path, data),
            "hash_content": content_hash,
            "hash_text": text_hash,
        })

    # Use shared three-tier grouping
    raw_groups = group_duplicates(file_records)

    dupe_groups = []
    total_files_to_move = 0
    space_savings_kb = 0

    for g in raw_groups:
        entries = g["entries"]
        keep = entries[0]
        move = entries[1:]

        group = {
            "group_hash": g["group_hash"],
            "group_hash_type": g["group_hash_type"],
            "decision": None,
            "keep": {
                "json": keep["json"],
                "size_kb": keep["size_kb"],
                "hash_content": keep.get("hash_content"),
            },
            "move": [
                {
                    "json": m["json"],
                    "size_kb": m["size_kb"],
                    "hash_content": m.get("hash_content"),
                }
                for m in move
            ],
        }
        dupe_groups.append(group)
        total_files_to_move += len(move)
        space_savings_kb += sum((m["size_kb"] or 0) for m in move)

    return {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "directory": str(directory),
        "scan_stats": {
            "scanned": scanned,
            "skipped_no_hash": skipped_no_hash,
        },
        "summary": {
            "total_groups": len(dupe_groups),
            "total_files_to_move": total_files_to_move,
            "space_savings_kb": space_savings_kb,
            "approved": 0,
            "rejected": 0,
            "pending": len(dupe_groups),
        },
        "groups": dupe_groups,
    }


def plan(directory: Path):
    """Generate deduplication plan and write to disk."""
    plan_data = scan_directory(directory)

    plan_path = directory / PLAN_FILENAME
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan_data, f, indent=4, ensure_ascii=False)

    # Print summary
    stats = plan_data["scan_stats"]
    groups = plan_data["groups"]
    summary = plan_data["summary"]

    print(f"\nScanned {stats['scanned']} files ({stats['skipped_no_hash']} without any hash)")
    print(f"Found {len(groups)} duplicate groups:")
    print()

    for group in groups:
        keep = group["keep"]
        print(f"  {group['group_hash_type']}={group['group_hash']}:")
        print(f"    KEEP: {keep['json']} ({keep['size_kb']} KB)")
        for m in group["move"]:
            print(f"    MOVE: {m['json']} ({m['size_kb']} KB)")
        print()

    print(f"Summary: {summary['total_files_to_move']} files to move, ~{summary['space_savings_kb']} KB savings")
    print(f"Plan written to: {plan_path}")
    print(f"\nReview the plan, then run: python scripts/deduplicate.py execute {directory}")


def execute(directory: Path, dry_run: bool = False, dupes_dir: Path | None = None):
    """Execute deduplication plan.

    Args:
        directory: The directory containing the plan file.
        dry_run: If True, preview without moving files.
        dupes_dir: Optional custom directory for moved duplicates.
                   Defaults to <directory>/../_dupes/.
    """
    plan_path = directory / PLAN_FILENAME
    if not plan_path.exists():
        print(f"Error: No plan found at {plan_path}")
        print(f"Run 'python scripts/deduplicate.py plan {directory}' first.")
        sys.exit(1)

    with open(plan_path, "r", encoding="utf-8") as f:
        plan_data = json.load(f)

    groups = plan_data["groups"]
    if not groups:
        print("Plan has no duplicate groups. Nothing to do.")
        return

    if dupes_dir is None:
        dupes_dir = directory.parent / DUPES_DIRNAME
    if not dry_run:
        dupes_dir.mkdir(exist_ok=True)

    if dry_run:
        print("DRY RUN — no files will be moved\n")

    # Detect whether any decisions are present
    has_decisions = any(g.get("decision") is not None for g in groups)

    moved_count = 0
    skipped_count = 0
    errors = 0

    for group_idx, group in enumerate(groups):
        if has_decisions:
            decision = group.get("decision")
            group_label = f"{group.get('group_hash_type', 'hash_text')}={group.get('group_hash', group.get('hash_text', '?'))}"
            if decision == "rejected":
                print(f"  [SKIP-REJECTED] {group_label}")
                skipped_count += 1
                continue
            elif decision != "approved":
                print(f"  [SKIP-PENDING] {group_label}")
                skipped_count += 1
                continue

        for entry in group["move"]:
            json_name = entry["json"]
            json_path = directory / json_name

            if not json_path.exists():
                print(f"  [SKIP] {json_name}: JSON not found")
                continue

            # Collect all related files to move
            files_to_move = [json_path]

            # Companion file (PDF/XLSX)
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                companion = _find_companion(json_path, data)
                if companion and companion.exists():
                    files_to_move.append(companion)
            except Exception:
                pass

            # Extra sidecars (.embeddings.json, .reconciliation.json)
            stem = json_path.stem
            for extra_suffix in (".embeddings.json", ".reconciliation.json"):
                extra = json_path.parent / (stem + extra_suffix)
                if extra.exists():
                    files_to_move.append(extra)

            for src in files_to_move:
                dst = dupes_dir / src.name
                # Handle name conflicts
                if dst.exists():
                    base = dst.stem
                    suffix = dst.suffix
                    counter = 2
                    while dst.exists():
                        dst = dupes_dir / f"{base}_{counter}{suffix}"
                        counter += 1

                if dry_run:
                    print(f"  [WOULD MOVE] {src.name}")
                else:
                    try:
                        src.rename(dst)
                        print(f"  [MOVED] {src.name}")
                    except Exception as e:
                        print(f"  [ERROR] {src.name}: {e}")
                        errors += 1
                        continue

            moved_count += 1

    print(f"\nSummary: {moved_count} duplicate entries processed, {skipped_count} skipped, {errors} errors")
    if not dry_run:
        print(f"Duplicates moved to: {dupes_dir}")


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} plan|execute <directory> [--dry-run]")
        sys.exit(1)

    command = sys.argv[1]
    directory = Path(sys.argv[2])
    dry_run = "--dry-run" in sys.argv

    if not directory.is_dir():
        print(f"Error: {directory} is not a directory")
        sys.exit(1)

    if command == "plan":
        plan(directory)
    elif command == "execute":
        execute(directory, dry_run)
    else:
        print(f"Unknown command: {command}. Use 'plan' or 'execute'.")
        sys.exit(1)


if __name__ == "__main__":
    main()
