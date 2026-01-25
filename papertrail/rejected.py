"""Rejected values manager for tracking normalization rejections."""

from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml


class RejectedValuesManager:
    """Track rejected normalizations for review.

    When the LLM suggests a canonical value that's not in the allowed list,
    we log it here instead of silently using $UNKNOWN$. This allows users
    to review and either add new canonicals or create mappings.
    """

    FIELDS = ("document_types", "issuing_parties")

    def __init__(self, rejected_path: Path):
        """Initialize the rejected values manager.

        Args:
            rejected_path: Path to the YAML rejected values file
        """
        self.path = rejected_path
        self.data = self._load()

    def _load(self) -> dict:
        """Load rejected values from YAML file, creating empty structure if missing."""
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        # Ensure structure exists for all fields
        for field in self.FIELDS:
            if field not in data:
                data[field] = []

        return data

    def _save(self) -> None:
        """Save rejected values to YAML file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(self.data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def add_rejected(
        self,
        field: str,
        normalized: str,
        raw: str,
        save: bool = True
    ) -> bool:
        """Add a rejected normalization.

        Args:
            field: Field name ('document_types' or 'issuing_parties')
            normalized: The canonical value suggested by the LLM (that was rejected)
            raw: The original raw value from extraction
            save: If True, save to file immediately

        Returns:
            True if added (new rejection), False if duplicate
        """
        if field not in self.FIELDS:
            return False

        # Check for duplicates (same raw + normalized combo)
        for entry in self.data[field]:
            if entry.get("raw") == raw and entry.get("normalized") == normalized:
                # Update timestamp on duplicate
                entry["last_seen"] = datetime.now().isoformat()
                entry["count"] = entry.get("count", 1) + 1
                if save:
                    self._save()
                return False

        # Add new entry
        self.data[field].append({
            "raw": raw,
            "normalized": normalized,
            "first_seen": datetime.now().isoformat(),
            "last_seen": datetime.now().isoformat(),
            "count": 1,
        })

        if save:
            self._save()
        return True

    def get_rejected(self, field: str) -> list[dict]:
        """Get all rejected values for a field.

        Args:
            field: Field name ('document_types' or 'issuing_parties')

        Returns:
            List of rejected value entries
        """
        if field not in self.FIELDS:
            return []
        return list(self.data.get(field, []))

    def remove_rejected(
        self,
        field: str,
        raw: str,
        normalized: Optional[str] = None,
        save: bool = True
    ) -> bool:
        """Remove a rejected value entry.

        Args:
            field: Field name ('document_types' or 'issuing_parties')
            raw: The raw value to remove
            normalized: If provided, only remove if normalized matches
            save: If True, save to file immediately

        Returns:
            True if removed, False if not found
        """
        if field not in self.FIELDS:
            return False

        entries = self.data[field]
        original_len = len(entries)

        if normalized:
            self.data[field] = [
                e for e in entries
                if not (e.get("raw") == raw and e.get("normalized") == normalized)
            ]
        else:
            self.data[field] = [e for e in entries if e.get("raw") != raw]

        removed = len(self.data[field]) < original_len

        if removed and save:
            self._save()
        return removed

    def clear_field(self, field: str, save: bool = True) -> int:
        """Clear all rejected values for a field.

        Args:
            field: Field name ('document_types' or 'issuing_parties')
            save: If True, save to file immediately

        Returns:
            Number of entries cleared
        """
        if field not in self.FIELDS:
            return 0

        count = len(self.data[field])
        self.data[field] = []

        if save and count > 0:
            self._save()
        return count

    def get_stats(self) -> dict:
        """Get statistics about rejected values.

        Returns:
            Dict with counts for each field
        """
        return {field: len(self.data.get(field, [])) for field in self.FIELDS}

    def __len__(self) -> int:
        """Total number of rejected entries across all fields."""
        return sum(len(self.data.get(field, [])) for field in self.FIELDS)
