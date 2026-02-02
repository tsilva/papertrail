"""Mappings management tasks."""

import json
from pathlib import Path

from tqdm import tqdm

from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.mappings import MappingsManager
from papertrail.rejected import RejectedValuesManager
from papertrail.tasks import require_initialized

logger = get_logger('cli')


def task_bootstrap_mappings(processed_path: Path, mappings_mgr):
    """Populate mappings from existing metadata JSON files."""
    setup_task_logging(processed_path, "bootstrap_mappings")
    require_initialized(mappings_mgr, "Mappings manager")

    json_files = list(processed_path.rglob("*.json"))
    if not json_files:
        logger.info(f"No metadata files found in {processed_path}")
        return

    doc_type_count = 0
    issuer_count = 0
    skipped = 0

    for metadata_path in tqdm(json_files, desc="Scanning metadata"):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            doc_type_raw = data.get("document_type_raw")
            doc_type = data.get("document_type")
            issuing_party_raw = data.get("issuing_party_raw")
            issuing_party = data.get("issuing_party")

            if doc_type_raw and doc_type and doc_type != "$UNKNOWN$":
                existing = mappings_mgr.get_mapping(doc_type_raw, "document_types")
                if existing is None:
                    mappings_mgr.add_mapping(
                        doc_type_raw, doc_type, "document_types", confirmed=True, save=False
                    )
                    doc_type_count += 1

            if issuing_party_raw and issuing_party and issuing_party != "$UNKNOWN$":
                existing = mappings_mgr.get_mapping(issuing_party_raw, "issuing_parties")
                if existing is None:
                    mappings_mgr.add_mapping(
                        issuing_party_raw, issuing_party, "issuing_parties", confirmed=True, save=False
                    )
                    issuer_count += 1

        except Exception as e:
            skipped += 1
            if skipped <= 5:
                logger.warning(f"Skipping {metadata_path.name}: {e}")

    mappings_mgr._save()

    logger.info("Bootstrap complete:")
    logger.info(f"  Document type mappings added: {doc_type_count}")
    logger.info(f"  Issuing party mappings added: {issuer_count}")
    logger.info(f"  Files skipped: {skipped}")
    logger.info(f"Mappings saved to: {mappings_mgr.path}")

    stats = mappings_mgr.get_stats()
    logger.info("Current mappings stats:")
    for field, counts in stats.items():
        logger.info(f"  {field}: {counts['confirmed']} confirmed, {counts['auto']} auto, {counts['canonicals']} canonicals")


def task_review_mappings(mappings_mgr):
    """Interactive review of auto-added mappings."""
    require_initialized(mappings_mgr, "Mappings manager")

    doc_auto = mappings_mgr.get_auto_mappings("document_types")
    issuer_auto = mappings_mgr.get_auto_mappings("issuing_parties")

    total_pending = len(doc_auto) + len(issuer_auto)

    if total_pending == 0:
        logger.info("No auto-added mappings pending review.")
        stats = mappings_mgr.get_stats()
        logger.info("Current mappings stats:")
        for field, counts in stats.items():
            logger.info(f"  {field}: {counts['confirmed']} confirmed, {counts['auto']} auto")
        return

    print("=" * 60)
    print("AUTO-ADDED MAPPINGS AWAITING REVIEW")
    print("=" * 60)
    print()

    if doc_auto:
        print(f"Document Types ({len(doc_auto)} pending):")
        for i, (raw, canonical) in enumerate(doc_auto.items(), 1):
            print(f"  {i}. \"{raw}\" -> \"{canonical}\"")
        print()

    if issuer_auto:
        print(f"Issuing Parties ({len(issuer_auto)} pending):")
        for i, (raw, canonical) in enumerate(issuer_auto.items(), 1):
            print(f"  {i}. \"{raw}\" -> \"{canonical}\"")
        print()

    print("Options:")
    print("  [a] Confirm ALL mappings")
    print("  [r] Review one-by-one")
    print("  [q] Quit without changes")
    print()

    choice = input("Select option: ").strip().lower()

    if choice == 'a':
        doc_confirmed = mappings_mgr.confirm_all("document_types", save=False)
        issuer_confirmed = mappings_mgr.confirm_all("issuing_parties", save=True)
        logger.info(f"Confirmed {doc_confirmed} document type mappings and {issuer_confirmed} issuer mappings.")

    elif choice == 'r':
        _review_field_mappings(mappings_mgr, "document_types", doc_auto)
        _review_field_mappings(mappings_mgr, "issuing_parties", issuer_auto)
        logger.info("Review complete.")

    else:
        logger.info("No changes made.")


def _review_field_mappings(mappings_mgr, field: str, mappings_dict: dict):
    """Helper to review mappings for a single field."""
    if not mappings_dict:
        return

    field_label = "Document Type" if field == "document_types" else "Issuing Party"
    print(f"\n--- Reviewing {field_label} Mappings ---")

    for raw, canonical in list(mappings_dict.items()):
        print(f"\n\"{raw}\" -> \"{canonical}\"")
        print("  [c] Confirm  [e] Edit canonical  [r] Reject  [s] Skip")
        action = input("  Action: ").strip().lower()

        if action == 'c':
            mappings_mgr.confirm_mapping(raw, field, save=True)
            print("  Confirmed.")
        elif action == 'e':
            new_canonical = input("  Enter new canonical value: ").strip()
            if new_canonical:
                mappings_mgr.update_mapping(raw, new_canonical, field, confirm=True, save=True)
                print(f"  Updated to \"{new_canonical}\" and confirmed.")
            else:
                print("  No change (empty input).")
        elif action == 'r':
            mappings_mgr.reject_mapping(raw, field, save=True)
            print("  Rejected (removed).")
        else:
            print("  Skipped.")


def task_add_canonical(mappings_mgr, field: str, canonical: str):
    """Add a new canonical value to the mappings."""
    require_initialized(mappings_mgr, "Mappings manager")

    field_map = {
        "document_type": "document_types",
        "document_types": "document_types",
        "issuing_party": "issuing_parties",
        "issuing_parties": "issuing_parties",
    }

    normalized_field = field_map.get(field.lower())
    if not normalized_field:
        logger.error(f"Unknown field '{field}'. Use 'document_type' or 'issuing_party'.")
        return

    if mappings_mgr.add_canonical(normalized_field, canonical):
        logger.info(f"Added canonical '{canonical}' to {normalized_field}.")
        logger.info(f"Current canonicals: {', '.join(mappings_mgr.get_canonicals(normalized_field))}")
    else:
        logger.info(f"Canonical '{canonical}' already exists in {normalized_field}.")


def task_review_rejected(rejected_mgr: RejectedValuesManager, mappings_mgr: MappingsManager):
    """Interactive review of rejected normalization values."""
    require_initialized(rejected_mgr, "Rejected values manager")
    require_initialized(mappings_mgr, "Mappings manager")

    doc_rejected = rejected_mgr.get_rejected("document_types")
    issuer_rejected = rejected_mgr.get_rejected("issuing_parties")

    total_pending = len(doc_rejected) + len(issuer_rejected)

    if total_pending == 0:
        logger.info("No rejected values pending review.")
        stats = rejected_mgr.get_stats()
        logger.info(f"Rejected values stats: document_types={stats['document_types']}, issuing_parties={stats['issuing_parties']}")
        return

    print("=" * 60)
    print("REJECTED NORMALIZATIONS AWAITING REVIEW")
    print("=" * 60)
    print()
    print("These are values the LLM suggested but were not in the canonical list.")
    print("You can: add them as new canonicals, map them to existing ones, or ignore.")
    print()

    if doc_rejected:
        print(f"Document Types ({len(doc_rejected)} pending):")
        for i, entry in enumerate(doc_rejected, 1):
            count_str = f" (seen {entry['count']}x)" if entry.get('count', 1) > 1 else ""
            print(f"  {i}. \"{entry['raw']}\" -> LLM suggested \"{entry['normalized']}\"{count_str}")
        print()

    if issuer_rejected:
        print(f"Issuing Parties ({len(issuer_rejected)} pending):")
        for i, entry in enumerate(issuer_rejected, 1):
            count_str = f" (seen {entry['count']}x)" if entry.get('count', 1) > 1 else ""
            print(f"  {i}. \"{entry['raw']}\" -> LLM suggested \"{entry['normalized']}\"{count_str}")
        print()

    print("Options:")
    print("  [r] Review one-by-one")
    print("  [c] Clear all rejected values")
    print("  [q] Quit without changes")
    print()

    choice = input("Select option: ").strip().lower()

    if choice == 'c':
        doc_cleared = rejected_mgr.clear_field("document_types", save=False)
        issuer_cleared = rejected_mgr.clear_field("issuing_parties", save=True)
        logger.info(f"Cleared {doc_cleared} document type rejections and {issuer_cleared} issuer rejections.")

    elif choice == 'r':
        _review_rejected_field(rejected_mgr, mappings_mgr, "document_types", doc_rejected)
        _review_rejected_field(rejected_mgr, mappings_mgr, "issuing_parties", issuer_rejected)
        logger.info("Review complete.")

    else:
        logger.info("No changes made.")


def _review_rejected_field(
    rejected_mgr: RejectedValuesManager,
    mappings_mgr: MappingsManager,
    field: str,
    entries: list[dict]
):
    """Helper to review rejected values for a single field."""
    if not entries:
        return

    field_label = "Document Type" if field == "document_types" else "Issuing Party"
    canonicals = mappings_mgr.get_canonicals(field)

    print(f"\n--- Reviewing {field_label} Rejections ---")
    print(f"Current canonicals: {', '.join(canonicals[:20])}{'...' if len(canonicals) > 20 else ''}")

    for entry in list(entries):
        raw = entry['raw']
        normalized = entry['normalized']

        print(f"\nRaw: \"{raw}\"")
        print(f"LLM suggested: \"{normalized}\"")
        print("  [a] Add '{normalized}' as new canonical and create mapping")
        print("  [m] Map to existing canonical")
        print("  [i] Ignore (remove from rejected list)")
        print("  [s] Skip")
        action = input("  Action: ").strip().lower()

        if action == 'a':
            mappings_mgr.add_canonical(field, normalized, save=False)
            mappings_mgr.add_mapping(raw, normalized, field, confirmed=True, save=True)
            rejected_mgr.remove_rejected(field, raw, normalized, save=True)
            print(f"  Added canonical '{normalized}' and mapped '{raw}' -> '{normalized}'")

        elif action == 'm':
            print(f"  Available canonicals: {', '.join(canonicals)}")
            new_canonical = input("  Enter canonical to map to: ").strip()
            if new_canonical in canonicals:
                mappings_mgr.add_mapping(raw, new_canonical, field, confirmed=True, save=True)
                rejected_mgr.remove_rejected(field, raw, normalized, save=True)
                print(f"  Mapped '{raw}' -> '{new_canonical}'")
            elif new_canonical:
                confirm = input(f"  '{new_canonical}' not in canonicals. Add it? [y/n]: ").strip().lower()
                if confirm == 'y':
                    mappings_mgr.add_canonical(field, new_canonical, save=False)
                    mappings_mgr.add_mapping(raw, new_canonical, field, confirmed=True, save=True)
                    rejected_mgr.remove_rejected(field, raw, normalized, save=True)
                    print(f"  Added canonical '{new_canonical}' and mapped '{raw}' -> '{new_canonical}'")
                else:
                    print("  No change.")
            else:
                print("  No change (empty input).")

        elif action == 'i':
            rejected_mgr.remove_rejected(field, raw, normalized, save=True)
            print("  Removed from rejected list.")

        else:
            print("  Skipped.")
