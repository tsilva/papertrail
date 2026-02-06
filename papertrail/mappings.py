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

    Rules: casefold, NFKD normalize, ASCII-encode (strip accents),
    strip punctuation (keep letters, digits, spaces, hyphens),
    remove underscores, collapse whitespace/hyphens to single hyphen,
    strip. Produces ASCII-only output. Idempotent.
    """
    s = s.casefold()
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[^\w\s-]', '', s)
    s = s.replace('_', '')
    return re.sub(r'[\s-]+', '-', s).strip('-')


class MappingsManager:
    """Load, query, and persist raw → canonical mappings.

    Uses a flat mappings dict per field. Canonical values are derived
    on-the-fly from the unique set of mapping values.
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

        for field in self.FIELDS:
            if field not in data:
                data[field] = {}
            if "mappings" not in data[field]:
                data[field]["mappings"] = {}

        return data

    def _save(self) -> None:
        """Save mappings to YAML file, sorting each field's mappings alphabetically."""
        for field in self.FIELDS:
            if "mappings" in self.data[field]:
                self.data[field]["mappings"] = dict(
                    sorted(self.data[field]["mappings"].items())
                )
        save_yaml(self.path, self.data)

    def _needs_migration(self) -> bool:
        """Check if mappings need migration from old format or key slugification."""
        for field in self.FIELDS:
            field_data = self.data.get(field, {})
            # Old format detection: has confirmed/auto/canonicals keys
            if any(k in field_data for k in ("confirmed", "auto", "canonicals")):
                return True
            # Slugification check on flat mappings
            for key in field_data.get("mappings", {}):
                if slugify_key(key) != key:
                    return True
        return False

    def _migrate_keys(self) -> None:
        """Migrate from old format and/or slugify all mapping keys.

        Old format migration: merge confirmed + auto → single mappings dict
        (confirmed wins on collision), drop canonicals list.

        Slugification: normalize all keys, merge collisions (keep first).
        """
        for field in self.FIELDS:
            field_data = self.data[field]

            # Detect old format and merge confirmed + auto
            if any(k in field_data for k in ("confirmed", "auto", "canonicals")):
                confirmed = field_data.pop("confirmed", {})
                auto = field_data.pop("auto", {})
                field_data.pop("canonicals", None)

                # Merge: auto first, then confirmed overwrites (confirmed wins)
                merged = {}
                merged.update(auto)
                merged.update(confirmed)

                # Merge with any existing flat mappings (shouldn't happen, but safe)
                existing = field_data.get("mappings", {})
                existing.update(merged)
                field_data["mappings"] = existing

            # Slugify all keys
            old_mappings = field_data.get("mappings", {})
            new_mappings = {}
            for key, canonical in old_mappings.items():
                slug = slugify_key(key)
                if not slug:
                    continue
                if slug in new_mappings:
                    if new_mappings[slug] != canonical:
                        logger.warning(
                            "[MAPPING-MIGRATE] Collision in %s: "
                            "%r→%r and %r→%r both slugify to %r, keeping %r",
                            field, key, canonical,
                            slug, new_mappings[slug], slug, new_mappings[slug],
                        )
                else:
                    new_mappings[slug] = canonical
            field_data["mappings"] = new_mappings

        self._save()
        logger.info("[MAPPING-MIGRATE] Migrated mapping keys in %s", self.path)

    @validate_field(default_return=None)
    def get_mapping(self, raw_value: str, field: str) -> Optional[str]:
        """Check if raw value has a known mapping.

        Args:
            raw_value: The raw extracted value
            field: Field name ('document_types' or 'issuing_parties')

        Returns:
            Canonical value if found, None otherwise
        """
        raw_value = slugify_key(raw_value)
        if not raw_value:
            return None
        return self.data.get(field, {}).get("mappings", {}).get(raw_value)

    @validate_field(default_return=None)
    def add_mapping(
        self,
        raw_value: str,
        canonical: str,
        field: str,
        save: bool = True
    ) -> None:
        """Add a new mapping.

        Args:
            raw_value: The raw extracted value
            canonical: The canonical value to map to
            field: Field name ('document_types' or 'issuing_parties')
            save: If True, save to file immediately
        """
        with self._lock:
            raw_value = slugify_key(raw_value)
            if not raw_value:
                return
            self.data[field]["mappings"][raw_value] = canonical

            if save:
                self._save()

    @validate_field(default_return=[])
    def get_canonicals(self, field: str) -> list[str]:
        """Get list of valid canonicals for a field, derived from mapping values.

        Args:
            field: Field name ('document_types' or 'issuing_parties')

        Returns:
            Sorted list of unique canonical values (always includes $UNKNOWN$)
        """
        values = set(self.data.get(field, {}).get("mappings", {}).values())
        values.add("$UNKNOWN$")
        return sorted(values)

    @validate_field(default_return=False)
    def update_mapping(
        self,
        raw_value: str,
        new_canonical: str,
        field: str,
        save: bool = True
    ) -> bool:
        """Update a mapping's canonical value.

        Args:
            raw_value: The raw value to update
            new_canonical: The new canonical value
            field: Field name ('document_types' or 'issuing_parties')
            save: If True, save to file immediately

        Returns:
            True if updated, False if not found
        """
        raw_value = slugify_key(raw_value)
        if not raw_value:
            return False
        mappings = self.data[field].get("mappings", {})
        if raw_value not in mappings:
            return False

        mappings[raw_value] = new_canonical

        if save:
            self._save()
        return True

    def get_stats(self) -> dict:
        """Get statistics about the mappings.

        Returns:
            Dict with counts for each field
        """
        stats = {}
        for field in self.FIELDS:
            mappings = self.data[field].get("mappings", {})
            stats[field] = {
                "mappings": len(mappings),
                "canonicals": len(set(mappings.values()) | {"$UNKNOWN$"}),
            }
        return stats
