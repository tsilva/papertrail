"""Filename length audit helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


DEFAULT_FILENAME_MAX_CHARS = 60


@dataclass(frozen=True)
class FilenameLengthIssue:
    path: Path
    relative_path: str
    length: int
    max_length: int


def collect_long_filenames(
    root: Path,
    *,
    max_length: int = DEFAULT_FILENAME_MAX_CHARS,
) -> list[FilenameLengthIssue]:
    issues: list[FilenameLengthIssue] = []
    if not root.exists():
        return issues

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == ".DS_Store" or path.name.startswith("Icon"):
            continue
        length = len(path.name)
        if length <= max_length:
            continue
        try:
            relative_path = str(path.relative_to(root))
        except ValueError:
            relative_path = str(path)
        issues.append(
            FilenameLengthIssue(
                path=path,
                relative_path=relative_path,
                length=length,
                max_length=max_length,
            )
        )
    return issues


def format_long_filename_warning(
    issues: list[FilenameLengthIssue],
    *,
    max_items: int | None = None,
    markdown: bool = False,
) -> str:
    if not issues:
        return ""

    shown = issues if max_items is None else issues[:max_items]
    hidden_count = 0 if max_items is None else max(0, len(issues) - max_items)
    header = f"{len(issues)} file(s) exceed {issues[0].max_length} filename chars:"
    lines = [f"**Warning:** {header}" if markdown else header]
    for issue in shown:
        item = f"{issue.relative_path} ({issue.length})"
        lines.append(f"- `{item}`" if markdown else f"- {item}")
    if hidden_count:
        lines.append(f"- ... {hidden_count} more")
    return "\n".join(lines)
