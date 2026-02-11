"""Interactive confirmation prompts for new classification values."""

from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

from papertrail.console import get_console
from papertrail.logging_utils import get_logger

logger = get_logger('interactive')

# When False, new values are auto-accepted without prompting.
# Used in non-interactive contexts (e.g., multi-threaded sync, CI).
_interactive_enabled = True


def set_interactive(enabled: bool) -> None:
    """Enable or disable interactive prompts."""
    global _interactive_enabled
    _interactive_enabled = enabled


def is_interactive() -> bool:
    """Check if interactive prompts are enabled."""
    return _interactive_enabled


def confirm_classification(
    field_name: str,
    raw_value: str,
    suggested_value: str,
    known_values: set[str],
    file_name: str,
) -> str:
    """Interactive prompt for confirming new classification values.

    Shows the LLM's suggestion and offers options to accept, pick from existing
    values, enter a custom name, or skip ($UNKNOWN$).

    When interactive mode is disabled, auto-accepts the LLM suggestion.

    Returns the confirmed value.
    """
    if not _interactive_enabled:
        logger.debug(f"Non-interactive: auto-accepting {field_name}={suggested_value}")
        return suggested_value

    console = get_console().console

    # Build info table
    info = Table.grid(padding=(0, 1))
    info.add_column(style="bold")
    info.add_column()
    info.add_row("File:", f"[dim]{file_name}[/dim]")
    info.add_row("Raw:", f'[yellow]"{raw_value}"[/yellow]')
    info.add_row("LLM:", f"[cyan]{suggested_value}[/cyan]")

    console.print()
    console.print(Panel(
        info,
        title=f"[bold]New {field_name}[/bold]",
        border_style="cyan",
        padding=(0, 1),
    ))

    # Show options
    console.print(f"  [bold][1][/bold] Accept [cyan]{suggested_value}[/cyan]")
    console.print(f"  [bold][2][/bold] Pick from existing {field_name}s")
    console.print(f"  [bold][3][/bold] Enter custom name")
    console.print(f"  [bold][s][/bold] Skip ([dim]$UNKNOWN$[/dim])")
    console.print()

    choice = Prompt.ask("  Choice", choices=["1", "2", "3", "s"], default="1")

    if choice == "1":
        logger.debug(f"User accepted LLM suggestion: {field_name}={suggested_value}")
        return suggested_value

    if choice == "s":
        logger.debug(f"User skipped: {field_name}=$UNKNOWN$")
        return "$UNKNOWN$"

    if choice == "2":
        return _pick_from_existing(field_name, known_values, console)

    if choice == "3":
        return _enter_custom(field_name, console)

    return "$UNKNOWN$"


def _pick_from_existing(field_name: str, known_values: set[str], console) -> str:
    """Show numbered list of existing values for user to pick from."""
    sorted_values = sorted(v for v in known_values if v != "$UNKNOWN$")

    if not sorted_values:
        console.print("  [dim]No existing values to pick from.[/dim]")
        return _enter_custom(field_name, console)

    console.print()
    # Show in columns for readability
    col_width = max(len(v) for v in sorted_values) + 6
    cols = max(1, 80 // col_width)

    for i, value in enumerate(sorted_values, 1):
        end = "\n" if i % cols == 0 else ""
        console.print(f"  [bold]{i:>3}[/bold] {value}", end=end)
    if len(sorted_values) % cols != 0:
        console.print()

    console.print()
    raw = Prompt.ask(f"  Pick # (or 'c' for custom)")

    if raw.lower() == "c":
        return _enter_custom(field_name, console)

    try:
        idx = int(raw) - 1
        if 0 <= idx < len(sorted_values):
            picked = sorted_values[idx]
            logger.debug(f"User picked existing: {field_name}={picked}")
            return picked
    except ValueError:
        pass

    console.print("  [red]Invalid choice[/red], falling back to $UNKNOWN$")
    return "$UNKNOWN$"


def _enter_custom(field_name: str, console) -> str:
    """Prompt user for a custom value."""
    value = Prompt.ask(f"  Enter {field_name}").strip()
    if not value:
        return "$UNKNOWN$"
    logger.debug(f"User entered custom: {field_name}={value}")
    return value
