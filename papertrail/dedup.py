"""Shared deduplication logic for extraction pipeline and deduplicate script."""

from __future__ import annotations

from typing import Callable, TypeVar

T = TypeVar("T")


def group_duplicates(file_records: list[dict]) -> list[dict]:
    """Group file records into duplicate sets using three-tier hash hierarchy.

    Primary grouping uses ``hash_content`` (authoritative visual dedup).
    Files without ``hash_content`` fall back to ``hash_text``.
    Files with neither hash are excluded.

    Within each group, entries are sorted by ``size_kb`` ascending so the
    smallest (most compressed) file is first — intended as the "keep" entry.

    Args:
        file_records: List of dicts, each with at least ``hash_content``,
            ``hash_text``, and ``size_kb`` keys.

    Returns:
        List of group dicts (only groups with 2+ members), each containing:
        - ``group_hash``: The hash value used for grouping
        - ``group_hash_type``: ``"hash_content"`` or ``"hash_text"``
        - ``entries``: List of record dicts, sorted by size ascending
    """
    content_groups: dict[str, list[dict]] = {}
    text_only_groups: dict[str, list[dict]] = {}
    skipped = 0

    for record in file_records:
        content_hash = record.get("hash_content")
        text_hash = record.get("hash_text")

        if content_hash:
            content_groups.setdefault(content_hash, []).append(record)
        elif text_hash:
            text_only_groups.setdefault(text_hash, []).append(record)
        else:
            skipped += 1

    groups = []

    for hash_val, entries in sorted(content_groups.items()):
        if len(entries) < 2:
            continue
        entries.sort(key=lambda e: e.get("size_kb") or 0)
        groups.append({
            "group_hash": hash_val,
            "group_hash_type": "hash_content",
            "entries": entries,
        })

    for hash_val, entries in sorted(text_only_groups.items()):
        if len(entries) < 2:
            continue
        entries.sort(key=lambda e: e.get("size_kb") or 0)
        groups.append({
            "group_hash": hash_val,
            "group_hash_type": "hash_text",
            "entries": entries,
        })

    return groups


def dedup_batch(
    items: list[T],
    hash_fn: Callable[[T], str | None],
    seen: set[str] | None = None,
    label: str = "",
) -> tuple[list[T], int]:
    """Filter a list keeping only the first occurrence per hash value.

    Items where ``hash_fn`` returns ``None`` pass through (can't dedup at
    this tier).  When *seen* is provided it is updated in-place and shared
    across calls.

    Args:
        items: Items to deduplicate.
        hash_fn: Callable returning a hash string (or ``None``) for an item.
        seen: Optional set of already-seen hashes (mutated in-place).
        label: Human-readable label for logging context.

    Returns:
        ``(deduplicated_list, removed_count)``
    """
    if seen is None:
        seen = set()

    result: list[T] = []
    removed = 0

    for item in items:
        h = hash_fn(item)
        if h is None:
            result.append(item)
        elif h in seen:
            removed += 1
        else:
            seen.add(h)
            result.append(item)

    return result, removed
