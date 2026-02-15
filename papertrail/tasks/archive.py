"""Archive documents by moving them out of the active processed directory."""

from pathlib import Path

from papertrail.console import get_console
from papertrail.metadata import find_companion_file, iter_json_files


def task_archive(processed_path: Path, digests: list[str], dry_run: bool = False) -> None:
    """Archive documents by hash_file digest.

    Moves JSON sidecar + companion file + extra sidecars to _archived/ sibling directory.
    """
    console = get_console()
    archive_dir = processed_path.parent / "_archived"

    # Build hash_file → json_path lookup
    hash_to_json: dict[str, Path] = {}
    for json_path, data in iter_json_files(processed_path):
        hf = data.get("hash_file")
        if hf:
            hash_to_json[hf] = json_path

    if not dry_run:
        archive_dir.mkdir(exist_ok=True)

    if dry_run:
        console.info("Dry run — no files will be moved", indent=False)

    found = 0
    moved = 0
    not_found = []

    for digest in digests:
        json_path = hash_to_json.get(digest)
        if not json_path:
            not_found.append(digest)
            console.warning(f"[NOT FOUND] {digest}", indent=False)
            continue

        found += 1
        data = None
        try:
            from papertrail.metadata import load_json_data
            data = load_json_data(json_path)
        except Exception:
            pass

        # Collect files to move
        files_to_move = [json_path]

        companion = find_companion_file(json_path, data)
        if companion and companion.exists():
            files_to_move.append(companion)

        stem = json_path.stem
        for extra_suffix in (".embeddings.json", ".reconciliation.json"):
            extra = json_path.parent / (stem + extra_suffix)
            if extra.exists():
                files_to_move.append(extra)

        for src in files_to_move:
            dst = archive_dir / src.name
            if dst.exists():
                base, suffix = dst.stem, dst.suffix
                counter = 2
                while dst.exists():
                    dst = archive_dir / f"{base}_{counter}{suffix}"
                    counter += 1

            if dry_run:
                console.detail(f"[WOULD MOVE] {src.name}", indent=False)
            else:
                src.rename(dst)
                console.detail(f"[MOVED] {src.name}", indent=False)

        moved += 1

    # Summary
    console.info(
        f"Archive: {found} found, {moved} archived, {len(not_found)} not found",
        indent=False,
    )
    if not dry_run and moved > 0:
        console.detail(f"Archived to: {archive_dir}", indent=False)
