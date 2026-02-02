"""Shared YAML load/save utilities."""

from pathlib import Path

import yaml


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
