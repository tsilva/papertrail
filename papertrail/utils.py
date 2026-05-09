"""Shared utilities: dates, text, patterns, YAML."""

import fnmatch
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

def compute_month_range(months: int) -> list[str]:
    """Return list of YYYY-MM strings from N months back to current month."""
    today = datetime.now()
    result = []
    for i in range(months - 1, -1, -1):
        year = today.year
        month = today.month - i
        while month <= 0:
            month += 12
            year -= 1
        result.append(f"{year:04d}-{month:02d}")
    return result


def month_to_date_range(months: list[str]) -> tuple[datetime, datetime]:
    """Convert YYYY-MM list to (start_date, end_date) for date-bounded queries."""
    earliest = min(months)
    latest = max(months)
    start = datetime.strptime(earliest, "%Y-%m").replace(day=1)
    latest_dt = datetime.strptime(latest, "%Y-%m")
    today = datetime.now()
    if latest_dt.year == today.year and latest_dt.month == today.month:
        end = today
    else:
        if latest_dt.month == 12:
            end = latest_dt.replace(year=latest_dt.year + 1, month=1, day=1)
        else:
            end = latest_dt.replace(month=latest_dt.month + 1, day=1)
    return start, end


def strip_diacritics(s: str) -> str:
    """Remove diacritics/accents from a string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def compact_match_key(value: object) -> str:
    return "".join(char for char in strip_diacritics(str(value or "")).lower() if char.isalnum())


_REGEX_INDICATORS = (
    r'\d', r'\w', r'\s', r'\b', r'\B',
    r'^', r'$', r'+', r'{', r'}',
    r'(?', r'\A', r'\Z', r'|',
)


def is_regex_pattern(pattern: str) -> bool:
    """Detect if pattern uses regex-specific syntax."""
    return any(indicator in pattern for indicator in _REGEX_INDICATORS)


def make_matcher(pattern: str, use_search: bool = False) -> Callable[[str], bool]:
    """Create a matcher function from a glob or regex pattern."""
    if is_regex_pattern(pattern):
        compiled = re.compile(pattern)
        return (lambda name: bool(compiled.search(name))) if use_search else (lambda name: bool(compiled.fullmatch(name)))
    if use_search and not any(c in pattern for c in '*?['):
        return lambda name: pattern in name
    return lambda name: fnmatch.fnmatch(name, pattern)


def load_yaml(path: Path) -> dict:
    """Load YAML file, returning empty dict if missing."""
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


def save_yaml(path: Path, data: dict) -> None:
    """Save dict to YAML file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
