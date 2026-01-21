"""Mappings manager for raw → canonical value persistence."""

from pathlib import Path
from typing import Optional

import yaml


class MappingsManager:
    """Load, query, and persist raw → canonical mappings.

    Supports two-tier lookup:
    1. Check 'confirmed' mappings (user-validated)
    2. Check 'auto' mappings (LLM-generated, pending review)

    Also maintains a list of valid canonical values per field.
    """

    FIELDS = ("document_types", "issuing_parties")

    def __init__(self, mappings_path: Path):
        """Initialize the mappings manager.

        Args:
            mappings_path: Path to the YAML mappings file
        """
        self.path = mappings_path
        self.data = self._load()

    def _load(self) -> dict:
        """Load mappings from YAML file, creating empty structure if missing."""
        if self.path.exists():
            with open(self.path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        else:
            data = {}

        # Ensure structure exists for all fields
        for field in self.FIELDS:
            if field not in data:
                data[field] = {}
            if "confirmed" not in data[field]:
                data[field]["confirmed"] = {}
            if "auto" not in data[field]:
                data[field]["auto"] = {}
            if "canonicals" not in data[field]:
                data[field]["canonicals"] = ["$UNKNOWN$"]

        return data

    def _save(self) -> None:
        """Save mappings to YAML file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            yaml.dump(self.data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

    def get_mapping(self, raw_value: str, field: str) -> Optional[str]:
        """Check if raw value has a known mapping.

        Checks 'confirmed' mappings first, then 'auto'.

        Args:
            raw_value: The raw extracted value
            field: Field name ('document_types' or 'issuing_parties')

        Returns:
            Canonical value if found, None otherwise
        """
        if field not in self.FIELDS:
            return None

        section = self.data.get(field, {})
        # Check confirmed first, then auto
        result = section.get("confirmed", {}).get(raw_value)
        if result is not None:
            return result
        return section.get("auto", {}).get(raw_value)

    def add_mapping(
        self,
        raw_value: str,
        canonical: str,
        field: str,
        confirmed: bool = False,
        save: bool = True
    ) -> None:
        """Add a new mapping.

        Args:
            raw_value: The raw extracted value
            canonical: The canonical value to map to
            field: Field name ('document_types' or 'issuing_parties')
            confirmed: If True, add to 'confirmed', else 'auto'
            save: If True, save to file immediately
        """
        if field not in self.FIELDS:
            return

        tier = "confirmed" if confirmed else "auto"
        self.data[field][tier][raw_value] = canonical

        # Ensure canonical is in the canonicals list
        if canonical not in self.data[field]["canonicals"]:
            self.data[field]["canonicals"].append(canonical)

        if save:
            self._save()

    def get_canonicals(self, field: str) -> list[str]:
        """Get list of valid canonicals for a field.

        Args:
            field: Field name ('document_types' or 'issuing_parties')

        Returns:
            List of canonical values
        """
        if field not in self.FIELDS:
            return []
        return self.data.get(field, {}).get("canonicals", [])

    def add_canonical(self, field: str, canonical: str, save: bool = True) -> bool:
        """Add a new canonical value.

        Args:
            field: Field name ('document_types' or 'issuing_parties')
            canonical: The canonical value to add
            save: If True, save to file immediately

        Returns:
            True if added, False if already exists
        """
        if field not in self.FIELDS:
            return False

        canonicals = self.data[field]["canonicals"]
        if canonical in canonicals:
            return False

        canonicals.append(canonical)
        if save:
            self._save()
        return True

    def confirm_mapping(self, raw_value: str, field: str, save: bool = True) -> bool:
        """Move a mapping from 'auto' to 'confirmed'.

        Args:
            raw_value: The raw value to confirm
            field: Field name ('document_types' or 'issuing_parties')
            save: If True, save to file immediately

        Returns:
            True if moved, False if not found in auto
        """
        if field not in self.FIELDS:
            return False

        auto = self.data[field].get("auto", {})
        if raw_value not in auto:
            return False

        canonical = auto.pop(raw_value)
        self.data[field]["confirmed"][raw_value] = canonical

        if save:
            self._save()
        return True

    def reject_mapping(self, raw_value: str, field: str, save: bool = True) -> bool:
        """Remove a mapping from 'auto'.

        Args:
            raw_value: The raw value to reject
            field: Field name ('document_types' or 'issuing_parties')
            save: If True, save to file immediately

        Returns:
            True if removed, False if not found
        """
        if field not in self.FIELDS:
            return False

        auto = self.data[field].get("auto", {})
        if raw_value not in auto:
            return False

        del auto[raw_value]

        if save:
            self._save()
        return True

    def update_mapping(
        self,
        raw_value: str,
        new_canonical: str,
        field: str,
        confirm: bool = True,
        save: bool = True
    ) -> bool:
        """Update a mapping's canonical value and optionally confirm it.

        Args:
            raw_value: The raw value to update
            new_canonical: The new canonical value
            field: Field name ('document_types' or 'issuing_parties')
            confirm: If True, move to 'confirmed' after updating
            save: If True, save to file immediately

        Returns:
            True if updated, False if not found
        """
        if field not in self.FIELDS:
            return False

        # Check both tiers
        auto = self.data[field].get("auto", {})
        confirmed = self.data[field].get("confirmed", {})

        found_in = None
        if raw_value in auto:
            found_in = "auto"
        elif raw_value in confirmed:
            found_in = "confirmed"
        else:
            return False

        # Remove from current location
        if found_in == "auto":
            del auto[raw_value]
        else:
            del confirmed[raw_value]

        # Add to target tier
        target_tier = "confirmed" if confirm else found_in
        self.data[field][target_tier][raw_value] = new_canonical

        # Ensure canonical is in the list
        if new_canonical not in self.data[field]["canonicals"]:
            self.data[field]["canonicals"].append(new_canonical)

        if save:
            self._save()
        return True

    def get_auto_mappings(self, field: str) -> dict[str, str]:
        """Get all auto-added mappings pending review.

        Args:
            field: Field name ('document_types' or 'issuing_parties')

        Returns:
            Dict of raw_value -> canonical for auto mappings
        """
        if field not in self.FIELDS:
            return {}
        return dict(self.data.get(field, {}).get("auto", {}))

    def get_confirmed_mappings(self, field: str) -> dict[str, str]:
        """Get all confirmed mappings.

        Args:
            field: Field name ('document_types' or 'issuing_parties')

        Returns:
            Dict of raw_value -> canonical for confirmed mappings
        """
        if field not in self.FIELDS:
            return {}
        return dict(self.data.get(field, {}).get("confirmed", {}))

    def get_stats(self) -> dict:
        """Get statistics about the mappings.

        Returns:
            Dict with counts for each field and tier
        """
        stats = {}
        for field in self.FIELDS:
            stats[field] = {
                "confirmed": len(self.data[field].get("confirmed", {})),
                "auto": len(self.data[field].get("auto", {})),
                "canonicals": len(self.data[field].get("canonicals", [])),
            }
        return stats

    def confirm_all(self, field: str, save: bool = True) -> int:
        """Confirm all auto mappings for a field.

        Args:
            field: Field name ('document_types' or 'issuing_parties')
            save: If True, save to file immediately

        Returns:
            Number of mappings confirmed
        """
        if field not in self.FIELDS:
            return 0

        auto = self.data[field].get("auto", {})
        confirmed = self.data[field].get("confirmed", {})

        count = len(auto)
        confirmed.update(auto)
        self.data[field]["auto"] = {}

        if save and count > 0:
            self._save()
        return count
