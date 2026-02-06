"""Mappings manager for raw → canonical value persistence."""

import logging
import re
import threading
import unicodedata
from pathlib import Path
from typing import Optional

from papertrail.constants import validate_field
from papertrail.constants import FIELDS
from papertrail.yaml_utils import load_yaml, save_yaml

logger = logging.getLogger(__name__)


def slugify_key(s: str) -> str:
    """Normalize a raw value into a stable mapping key.

    Rules: NFC normalize, casefold, strip punctuation (keep letters, digits,
    spaces, hyphens), remove underscores, collapse whitespace/hyphens to
    single hyphen, strip. Preserves accented characters. Idempotent.
    """
    s = unicodedata.normalize('NFC', s).casefold()
    s = re.sub(r'[^\w\s-]', '', s, flags=re.UNICODE)
    s = s.replace('_', '')
    return re.sub(r'[\s-]+', '-', s).strip('-')


class MappingsManager:
    """Load, query, and persist raw → canonical mappings.

    Supports two-tier lookup:
    1. Check 'confirmed' mappings (user-validated)
    2. Check 'auto' mappings (LLM-generated, pending review)

    Also maintains a list of valid canonical values per field.
    """

    FIELDS = FIELDS

    def __init__(self, mappings_path: Path):
        """Initialize the mappings manager.

        Args:
            mappings_path: Path to the YAML mappings file
        """
        self._lock = threading.Lock()
        self.path = mappings_path
        self.data = self._load()
        if self._needs_migration():
            self._migrate_keys()

    def _load(self) -> dict:
        """Load mappings from YAML file, creating empty structure if missing."""
        data = load_yaml(self.path)

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
        save_yaml(self.path, self.data)

    def _needs_migration(self) -> bool:
        """Check if any mapping keys need slugification."""
        for field in self.FIELDS:
            for tier in ("confirmed", "auto"):
                for key in self.data[field].get(tier, {}):
                    if slugify_key(key) != key:
                        return True
        return False

    def _migrate_keys(self) -> None:
        """Slugify all mapping keys, merging collisions.

        Collision rules:
        - Same slug, same canonical: merge silently
        - Same slug, different canonical: keep first, log warning
        - Same slug in both confirmed and auto: remove from auto
        """
        for field in self.FIELDS:
            for tier in ("confirmed", "auto"):
                old_mappings = self.data[field].get(tier, {})
                new_mappings = {}
                for key, canonical in old_mappings.items():
                    slug = slugify_key(key)
                    if not slug:
                        continue
                    if slug in new_mappings:
                        if new_mappings[slug] != canonical:
                            logger.warning(
                                "[MAPPING-MIGRATE] Collision in %s/%s: "
                                "%r→%r and %r→%r both slugify to %r, keeping %r",
                                field, tier, key, canonical,
                                slug, new_mappings[slug], slug, new_mappings[slug],
                            )
                    else:
                        new_mappings[slug] = canonical
                self.data[field][tier] = new_mappings

            # Remove auto entries that duplicate confirmed after slugification
            confirmed = self.data[field]["confirmed"]
            auto = self.data[field]["auto"]
            duplicates = [k for k in auto if k in confirmed]
            for k in duplicates:
                del auto[k]

        self._save()
        logger.info("[MAPPING-MIGRATE] Slugified mapping keys in %s", self.path)

    @validate_field(default_return=None)
    def get_mapping(self, raw_value: str, field: str) -> Optional[str]:
        """Check if raw value has a known mapping.

        Checks 'confirmed' mappings first, then 'auto'.

        Args:
            raw_value: The raw extracted value
            field: Field name ('document_types' or 'issuing_parties')

        Returns:
            Canonical value if found, None otherwise
        """
        raw_value = slugify_key(raw_value)
        if not raw_value:
            return None
        section = self.data.get(field, {})
        # Check confirmed first, then auto
        result = section.get("confirmed", {}).get(raw_value)
        if result is not None:
            return result
        return section.get("auto", {}).get(raw_value)

    @validate_field(default_return=None)
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
        with self._lock:
            raw_value = slugify_key(raw_value)
            if not raw_value:
                return
            tier = "confirmed" if confirmed else "auto"
            self.data[field][tier][raw_value] = canonical

            # Ensure canonical is in the canonicals list
            if canonical not in self.data[field]["canonicals"]:
                self.data[field]["canonicals"].append(canonical)

            if save:
                self._save()

    @validate_field(default_return=[])
    def get_canonicals(self, field: str) -> list[str]:
        """Get list of valid canonicals for a field.

        Args:
            field: Field name ('document_types' or 'issuing_parties')

        Returns:
            List of canonical values
        """
        return self.data.get(field, {}).get("canonicals", [])

    @validate_field(default_return=False)
    def add_canonical(self, field: str, canonical: str, save: bool = True) -> bool:
        """Add a new canonical value.

        Args:
            field: Field name ('document_types' or 'issuing_parties')
            canonical: The canonical value to add
            save: If True, save to file immediately

        Returns:
            True if added, False if already exists
        """
        canonicals = self.data[field]["canonicals"]
        if canonical in canonicals:
            return False

        canonicals.append(canonical)
        if save:
            self._save()
        return True

    @validate_field(default_return=False)
    def confirm_mapping(self, raw_value: str, field: str, save: bool = True) -> bool:
        """Move a mapping from 'auto' to 'confirmed'.

        Args:
            raw_value: The raw value to confirm
            field: Field name ('document_types' or 'issuing_parties')
            save: If True, save to file immediately

        Returns:
            True if moved, False if not found in auto
        """
        raw_value = slugify_key(raw_value)
        if not raw_value:
            return False
        auto = self.data[field].get("auto", {})
        if raw_value not in auto:
            return False

        canonical = auto.pop(raw_value)
        self.data[field]["confirmed"][raw_value] = canonical

        if save:
            self._save()
        return True

    @validate_field(default_return=False)
    def reject_mapping(self, raw_value: str, field: str, save: bool = True) -> bool:
        """Remove a mapping from 'auto'.

        Args:
            raw_value: The raw value to reject
            field: Field name ('document_types' or 'issuing_parties')
            save: If True, save to file immediately

        Returns:
            True if removed, False if not found
        """
        raw_value = slugify_key(raw_value)
        if not raw_value:
            return False
        auto = self.data[field].get("auto", {})
        if raw_value not in auto:
            return False

        del auto[raw_value]

        if save:
            self._save()
        return True

    @validate_field(default_return=False)
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
        raw_value = slugify_key(raw_value)
        if not raw_value:
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

    @validate_field(default_return={})
    def get_auto_mappings(self, field: str) -> dict[str, str]:
        """Get all auto-added mappings pending review.

        Args:
            field: Field name ('document_types' or 'issuing_parties')

        Returns:
            Dict of raw_value -> canonical for auto mappings
        """
        return dict(self.data.get(field, {}).get("auto", {}))


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

    @validate_field(default_return=0)
    def confirm_all(self, field: str, save: bool = True) -> int:
        """Confirm all auto mappings for a field.

        Args:
            field: Field name ('document_types' or 'issuing_parties')
            save: If True, save to file immediately

        Returns:
            Number of mappings confirmed
        """
        auto = self.data[field].get("auto", {})
        confirmed = self.data[field].get("confirmed", {})

        count = len(auto)
        confirmed.update(auto)
        self.data[field]["auto"] = {}

        if save and count > 0:
            self._save()
        return count
