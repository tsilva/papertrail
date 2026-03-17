"""Archive extraction adapter backed by unarch."""

from __future__ import annotations

from pathlib import Path

from unarch import extract_archives as unarch_extract_archives


def extract_archives(root: str | Path, *, passwords: list[str] | None = None) -> dict[str, int]:
    """Extract archives under ``root`` using papertrail's output naming semantics."""
    root_path = Path(root)
    return unarch_extract_archives(
        str(root_path),
        output_dir=str(root_path),
        passwords=passwords,
        show_progress=False,
        output_suffix="_archive",
        skip_existing=True,
    )
