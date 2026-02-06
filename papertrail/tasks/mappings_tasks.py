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
                    doc_type_raw, doc_type, "document_types", save=False
                )
                doc_type_count += 1

        if issuing_party_raw and issuing_party and issuing_party != "$UNKNOWN$":
            existing = mappings_mgr.get_mapping(issuing_party_raw, "issuing_parties")
            if existing is None:
                mappings_mgr.add_mapping(
                    issuing_party_raw, issuing_party, "issuing_parties", save=False
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
        logger.debug(f"  {field}: {counts['mappings']} mappings, {counts['canonicals']} canonicals")


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
            mappings_mgr.add_mapping(raw, normalized, field)
            rejected_mgr.remove_rejected(field, raw, normalized, save=True)
            console.console.print(f"  [green]Added mapping '{raw}' -> '{normalized}'[/green]")

        elif action == 'm':
            console.console.print(f"  [dim]Available canonicals: {', '.join(canonicals[:30])}{'...' if len(canonicals) > 30 else ''}[/dim]")
            new_canonical = Prompt.ask("  Enter canonical to map to")
            if new_canonical in canonicals:
                mappings_mgr.add_mapping(raw, new_canonical, field)
                rejected_mgr.remove_rejected(field, raw, normalized, save=True)
                console.console.print(f"  [green]Mapped '{raw}' -> '{new_canonical}'[/green]")
            elif new_canonical:
                if Confirm.ask(f"  '{new_canonical}' not in existing canonicals. Add mapping anyway?", default=False):
                    mappings_mgr.add_mapping(raw, new_canonical, field)
                    rejected_mgr.remove_rejected(field, raw, normalized, save=True)
                    console.console.print(f"  [green]Added mapping '{raw}' -> '{new_canonical}'[/green]")
                else:
                    console.console.print("  [dim]No change.[/dim]")
            else:
                console.console.print("  [dim]No change (empty input).[/dim]")

        elif action == 'i':
            rejected_mgr.remove_rejected(field, raw, normalized, save=True)
            console.console.print("  [yellow]Removed from rejected list.[/yellow]")

        else:
            console.console.print("  [dim]Skipped.[/dim]")
