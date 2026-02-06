"""Mappings management tasks."""

from pathlib import Path

from rich.prompt import Prompt, Confirm

from papertrail.console import get_console
from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.mappings import MappingsManager
from papertrail.metadata import load_validated_metadata
from papertrail.rejected import RejectedValuesManager
from papertrail.tasks import require_initialized

logger = get_logger('cli')


def task_bootstrap_mappings(processed_path: Path, mappings_mgr):
    """Populate mappings from existing metadata JSON files."""
    console = get_console()
    setup_task_logging(processed_path, "bootstrap_mappings")
    require_initialized(mappings_mgr, "Mappings manager")

    doc_type_count = 0
    issuer_count = 0
    processed_count = 0

    for metadata_path, _, data in load_validated_metadata(
        processed_path, require_pdf=False, validate=False, show_progress=True, progress_desc="Scanning metadata"
    ):
        processed_count += 1
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

    if processed_count == 0:
        console.warning("No metadata files found", indent=False)
        return

    mappings_mgr._save()

    console.success(
        f"{processed_count} files processed, "
        f"{doc_type_count} doc types + {issuer_count} issuers added",
        indent=False
    )

    logger.debug(f"Bootstrap complete:")
    logger.debug(f"  Document type mappings added: {doc_type_count}")
    logger.debug(f"  Issuing party mappings added: {issuer_count}")
    logger.debug(f"  Files processed: {processed_count}")
    logger.debug(f"Mappings saved to: {mappings_mgr.path}")

    stats = mappings_mgr.get_stats()
    logger.debug("Current mappings stats:")
    for field, counts in stats.items():
        logger.debug(f"  {field}: {counts['confirmed']} confirmed, {counts['auto']} auto, {counts['canonicals']} canonicals")


def task_review_mappings(mappings_mgr):
    """Interactive review of auto-added mappings."""
    console = get_console()
    require_initialized(mappings_mgr, "Mappings manager")

    doc_auto = mappings_mgr.get_auto_mappings("document_types")
    issuer_auto = mappings_mgr.get_auto_mappings("issuing_parties")

    total_pending = len(doc_auto) + len(issuer_auto)

    if total_pending == 0:
        console.info("No auto-added mappings pending review", indent=False)
        stats = mappings_mgr.get_stats()
        for field, counts in stats.items():
            console.detail(f"{field}: {counts['confirmed']} confirmed, {counts['auto']} auto", indent=False)
        return

    console.console.print()
    console.console.print("[bold cyan]AUTO-ADDED MAPPINGS AWAITING REVIEW[/bold cyan]")
    console.console.print()
    console.console.print("[dim]These are LLM-generated mappings from raw document text to canonical values.[/dim]")
    console.console.print("[dim]\"Original text from document\" -> \"normalized-canonical-value\"[/dim]")
    console.console.print()

    if doc_auto:
        console.console.print(f"[cyan]Document Types ({len(doc_auto)} pending):[/cyan]")
        for i, (raw, canonical) in enumerate(doc_auto.items(), 1):
            console.console.print(f"  {i}. \"{raw}\" [dim]->[/dim] \"{canonical}\"")
        console.console.print()

    if issuer_auto:
        console.console.print(f"[cyan]Issuing Parties ({len(issuer_auto)} pending):[/cyan]")
        for i, (raw, canonical) in enumerate(issuer_auto.items(), 1):
            console.console.print(f"  {i}. \"{raw}\" [dim]->[/dim] \"{canonical}\"")
        console.console.print()

    console.console.print("[bold]Options:[/bold]")
    console.console.print("  [green]a[/green] Approve all — confirm every mapping above")
    console.console.print("  [cyan]r[/cyan] Review one-by-one — inspect each mapping individually")
    console.console.print("  [dim]q[/dim] Quit — exit without changes")
    console.console.print()

    choice = Prompt.ask(
        "Select option",
        choices=["a", "r", "q"],
        default="q"
    )

    if choice == 'a':
        doc_confirmed = mappings_mgr.confirm_all("document_types", save=False)
        issuer_confirmed = mappings_mgr.confirm_all("issuing_parties", save=True)
        console.success(f"Confirmed {doc_confirmed} doc types + {issuer_confirmed} issuers", indent=False)

    elif choice == 'r':
        _review_field_mappings(mappings_mgr, "document_types", doc_auto)
        _review_field_mappings(mappings_mgr, "issuing_parties", issuer_auto)
        console.success("Review complete", indent=False)

    else:
        console.info("No changes made", indent=False)


def _review_field_mappings(mappings_mgr, field: str, mappings_dict: dict):
    """Helper to review mappings for a single field."""
    console = get_console()

    if not mappings_dict:
        return

    field_label = "Document Type" if field == "document_types" else "Issuing Party"
    console.console.print(f"\n[bold]--- Reviewing {field_label} Mappings ---[/bold]")

    for raw, canonical in list(mappings_dict.items()):
        console.console.print(f"\n\"{raw}\" [dim]->[/dim] \"{canonical}\"")
        console.console.print("  [green]c[/green] Confirm  [cyan]e[/cyan] Edit  [yellow]r[/yellow] Reject  [dim]s[/dim] Skip")

        action = Prompt.ask(
            "  Action",
            choices=["c", "e", "r", "s"],
            default="s"
        )

        if action == 'c':
            mappings_mgr.confirm_mapping(raw, field, save=True)
            console.console.print("  [green]Confirmed.[/green]")
        elif action == 'e':
            new_canonical = Prompt.ask("  Enter new canonical value")
            if new_canonical:
                mappings_mgr.update_mapping(raw, new_canonical, field, confirm=True, save=True)
                console.console.print(f"  [green]Updated to \"{new_canonical}\" and confirmed.[/green]")
            else:
                console.console.print("  [dim]No change (empty input).[/dim]")
        elif action == 'r':
            mappings_mgr.reject_mapping(raw, field, save=True)
            console.console.print("  [yellow]Rejected (removed).[/yellow]")
        else:
            console.console.print("  [dim]Skipped.[/dim]")


def task_add_canonical(mappings_mgr, field: str, canonical: str):
    """Add a new canonical value to the mappings."""
    console = get_console()
    require_initialized(mappings_mgr, "Mappings manager")

    field_map = {
        "document_type": "document_types",
        "document_types": "document_types",
        "issuing_party": "issuing_parties",
        "issuing_parties": "issuing_parties",
    }

    normalized_field = field_map.get(field.lower())
    if not normalized_field:
        console.error(f"Unknown field '{field}'. Use 'document_type' or 'issuing_party'.", indent=False)
        return

    if mappings_mgr.add_canonical(normalized_field, canonical):
        console.success(f"Added canonical '{canonical}' to {normalized_field}", indent=False)
        logger.debug(f"Current canonicals: {', '.join(mappings_mgr.get_canonicals(normalized_field))}")
    else:
        console.info(f"Canonical '{canonical}' already exists in {normalized_field}", indent=False)


def task_review_rejected(rejected_mgr: RejectedValuesManager, mappings_mgr: MappingsManager):
    """Interactive review of rejected normalization values."""
    console = get_console()
    require_initialized(rejected_mgr, "Rejected values manager")
    require_initialized(mappings_mgr, "Mappings manager")

    doc_rejected = rejected_mgr.get_rejected("document_types")
    issuer_rejected = rejected_mgr.get_rejected("issuing_parties")

    total_pending = len(doc_rejected) + len(issuer_rejected)

    if total_pending == 0:
        console.info("No rejected values pending review", indent=False)
        stats = rejected_mgr.get_stats()
        console.detail(f"Rejected values: document_types={stats['document_types']}, issuing_parties={stats['issuing_parties']}", indent=False)
        return

    console.console.print()
    console.console.print("[bold cyan]REJECTED NORMALIZATIONS AWAITING REVIEW[/bold cyan]")
    console.console.print()
    console.console.print("[dim]These are values the LLM suggested but were not in the canonical list.[/dim]")
    console.console.print("[dim]You can: add them as new canonicals, map them to existing ones, or ignore.[/dim]")
    console.console.print()

    if doc_rejected:
        console.console.print(f"[cyan]Document Types ({len(doc_rejected)} pending):[/cyan]")
        for i, entry in enumerate(doc_rejected, 1):
            count_str = f" [dim](seen {entry['count']}x)[/dim]" if entry.get('count', 1) > 1 else ""
            console.console.print(f"  {i}. \"{entry['raw']}\" [dim]->[/dim] LLM suggested \"{entry['normalized']}\"{count_str}")
        console.console.print()

    if issuer_rejected:
        console.console.print(f"[cyan]Issuing Parties ({len(issuer_rejected)} pending):[/cyan]")
        for i, entry in enumerate(issuer_rejected, 1):
            count_str = f" [dim](seen {entry['count']}x)[/dim]" if entry.get('count', 1) > 1 else ""
            console.console.print(f"  {i}. \"{entry['raw']}\" [dim]->[/dim] LLM suggested \"{entry['normalized']}\"{count_str}")
        console.console.print()

    console.console.print("[bold]Options:[/bold]")
    console.console.print("  [cyan]r[/cyan] Review one-by-one — inspect each rejection individually")
    console.console.print("  [yellow]c[/yellow] Clear all — remove all rejections from the list")
    console.console.print("  [dim]q[/dim] Quit — exit without changes")
    console.console.print()

    choice = Prompt.ask(
        "Select option",
        choices=["r", "c", "q"],
        default="q"
    )

    if choice == 'c':
        doc_cleared = rejected_mgr.clear_field("document_types", save=False)
        issuer_cleared = rejected_mgr.clear_field("issuing_parties", save=True)
        console.success(f"Cleared {doc_cleared} doc type + {issuer_cleared} issuer rejections", indent=False)

    elif choice == 'r':
        _review_rejected_field(rejected_mgr, mappings_mgr, "document_types", doc_rejected)
        _review_rejected_field(rejected_mgr, mappings_mgr, "issuing_parties", issuer_rejected)
        console.success("Review complete", indent=False)

    else:
        console.info("No changes made", indent=False)


def _review_rejected_field(
    rejected_mgr: RejectedValuesManager,
    mappings_mgr: MappingsManager,
    field: str,
    entries: list[dict]
):
    """Helper to review rejected values for a single field."""
    console = get_console()

    if not entries:
        return

    field_label = "Document Type" if field == "document_types" else "Issuing Party"
    canonicals = mappings_mgr.get_canonicals(field)

    console.console.print(f"\n[bold]--- Reviewing {field_label} Rejections ---[/bold]")
    console.console.print(f"[dim]Current canonicals: {', '.join(canonicals[:20])}{'...' if len(canonicals) > 20 else ''}[/dim]")

    for entry in list(entries):
        raw = entry['raw']
        normalized = entry['normalized']

        console.console.print(f"\nRaw: \"{raw}\"")
        console.console.print(f"LLM suggested: \"{normalized}\"")
        console.console.print("  [green]a[/green] Add as new canonical  [cyan]m[/cyan] Map to existing  [yellow]i[/yellow] Ignore  [dim]s[/dim] Skip")

        action = Prompt.ask(
            "  Action",
            choices=["a", "m", "i", "s"],
            default="s"
        )

        if action == 'a':
            mappings_mgr.add_canonical(field, normalized, save=False)
            mappings_mgr.add_mapping(raw, normalized, field, confirmed=True, save=True)
            rejected_mgr.remove_rejected(field, raw, normalized, save=True)
            console.console.print(f"  [green]Added canonical '{normalized}' and mapped '{raw}' -> '{normalized}'[/green]")

        elif action == 'm':
            console.console.print(f"  [dim]Available canonicals: {', '.join(canonicals[:30])}{'...' if len(canonicals) > 30 else ''}[/dim]")
            new_canonical = Prompt.ask("  Enter canonical to map to")
            if new_canonical in canonicals:
                mappings_mgr.add_mapping(raw, new_canonical, field, confirmed=True, save=True)
                rejected_mgr.remove_rejected(field, raw, normalized, save=True)
                console.console.print(f"  [green]Mapped '{raw}' -> '{new_canonical}'[/green]")
            elif new_canonical:
                if Confirm.ask(f"  '{new_canonical}' not in canonicals. Add it?", default=False):
                    mappings_mgr.add_canonical(field, new_canonical, save=False)
                    mappings_mgr.add_mapping(raw, new_canonical, field, confirmed=True, save=True)
                    rejected_mgr.remove_rejected(field, raw, normalized, save=True)
                    console.console.print(f"  [green]Added canonical '{new_canonical}' and mapped '{raw}' -> '{new_canonical}'[/green]")
                else:
                    console.console.print("  [dim]No change.[/dim]")
            else:
                console.console.print("  [dim]No change (empty input).[/dim]")

        elif action == 'i':
            rejected_mgr.remove_rejected(field, raw, normalized, save=True)
            console.console.print("  [yellow]Removed from rejected list.[/yellow]")

        else:
            console.console.print("  [dim]Skipped.[/dim]")
