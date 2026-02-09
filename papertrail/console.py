"""Rich-based console utilities for papertrail CLI output."""

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Generator, Sequence

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.rule import Rule
from rich.table import Table

from papertrail.logging_utils import suppress_console_logging


@dataclass
class StepResult:
    """Result of a step execution."""

    status: str = "pending"  # "success", "warning", "error", "pending"
    message: str = ""


class StepContext:
    """Context object for step progress, allowing result reporting.

    Provides methods to mark the step as successful, warning, or error,
    which will be displayed when the context manager exits.
    """

    def __init__(self) -> None:
        """Initialize the step context."""
        self._result = StepResult()

    def success(self, message: str = "") -> None:
        """Mark the step as successful.

        Args:
            message: Optional success message to display.
        """
        self._result.status = "success"
        self._result.message = message

    def warning(self, message: str = "") -> None:
        """Mark the step as a warning.

        Args:
            message: Warning message to display.
        """
        self._result.status = "warning"
        self._result.message = message

    def error(self, message: str = "") -> None:
        """Mark the step as an error.

        Args:
            message: Error message to display.
        """
        self._result.status = "error"
        self._result.message = message

    @property
    def result(self) -> StepResult:
        """Get the step result."""
        return self._result


class PapertrailConsole:
    """Rich-based console for papertrail CLI output.

    Provides styled output for:
    - Pipeline headers and step indicators
    - Progress bars (replacing tqdm)
    - Success/warning/error messages
    - Summary tables
    - Secondary/dim information
    """

    def __init__(self, console: Console | None = None) -> None:
        """Initialize the console.

        Args:
            console: Optional Rich Console instance. Creates one if not provided.
        """
        self.console = console or Console()
        self._step_counter = 0
        self._task_name: str | None = None

    def pipeline_header(self, profile_name: str, log_path: str | None = None) -> None:
        """Display the main pipeline header.

        Args:
            profile_name: Name of the active profile.
            log_path: Optional path to the log file.
        """
        self.console.print()
        self.console.print(
            Rule(" PIPELINE ", style="bold cyan", characters="=")
        )

        info_parts = [f"Profile: [cyan]{profile_name}[/cyan]"]
        if log_path:
            info_parts.append(f"Log: [dim]{log_path}[/dim]")

        self.console.print(" | ".join(info_parts))
        self.console.print()

    def pipeline_footer(self, elapsed_seconds: float | None = None) -> None:
        """Display the pipeline completion footer.

        Args:
            elapsed_seconds: Optional total elapsed time in seconds.
        """
        self.console.print()
        self.console.print(Rule(style="cyan", characters="="))

        if elapsed_seconds is not None:
            self.console.print(
                f"Pipeline completed in [bold]{elapsed_seconds:.1f}s[/bold]"
            )
        else:
            self.console.print("Pipeline completed")
        self.console.print()

    @contextmanager
    def task(
        self, name: str, description: str | None = None
    ) -> Generator[None, None, None]:
        """Context manager for a task with header/footer.

        Args:
            name: Task name for the header.
            description: Optional description shown below the name.

        Yields:
            None
        """
        self._task_name = name
        self._step_counter = 0

        self.console.print()
        self.console.print(Rule(f" {name} ", style="bold cyan"))
        if description:
            self.console.print(f"[dim]{description}[/dim]")
        self.console.print()

        try:
            yield
        finally:
            self._task_name = None

    def step(self, message: str, number: int | None = None) -> None:
        """Display a numbered step within a task.

        Args:
            message: Step description.
            number: Optional explicit step number. Auto-increments if not provided.
        """
        if number is None:
            self._step_counter += 1
            number = self._step_counter

        self.console.print(f"[cyan]{number}.[/cyan] {message}")

    @contextmanager
    def step_progress(self, message: str) -> Generator[StepContext, None, None]:
        """Context manager for a step with spinner progress indicator.

        Shows a spinner while the step is running, then replaces it with
        an appropriate symbol (\u2713/!/\u2717) when the step completes.

        Args:
            message: Step description to display.

        Yields:
            StepContext object with success(), warning(), and error() methods.

        Example:
            with console.step_progress("Download files") as step:
                # do work...
                step.success("Downloaded 5 files")
            # Output: \u2713 Download files \u2014 Downloaded 5 files
        """
        ctx = StepContext()

        with self.console.status(f"[cyan]{message}[/cyan]", spinner="dots") as status:
            with suppress_console_logging():
                try:
                    yield ctx
                except Exception:
                    # If an exception occurs and no status was set, mark as error
                    if ctx.result.status == "pending":
                        ctx.error("Failed with exception")
                    raise

        # Print the final result line
        result = ctx.result
        if result.status == "success":
            symbol = "[green]\u2713[/green]"
            msg_style = "[green]"
            detail_style = "[dim]"
        elif result.status == "warning":
            symbol = "[yellow]![/yellow]"
            msg_style = "[yellow]"
            detail_style = "[dim yellow]"
        elif result.status == "error":
            symbol = "[red]\u2717[/red]"
            msg_style = "[red]"
            detail_style = "[red]"
        else:
            # No result set, default to success
            symbol = "[green]\u2713[/green]"
            msg_style = "[green]"
            detail_style = "[dim]"

        if result.message:
            self.console.print(f"{symbol} {msg_style}{message}[/] \u2014 {detail_style}{result.message}[/]")
        else:
            self.console.print(f"{symbol} {msg_style}{message}[/]")

    def success(self, message: str, indent: bool = True) -> None:
        """Display a success message with green checkmark.

        Args:
            message: Success message.
            indent: Whether to indent the message.
        """
        prefix = "   " if indent else ""
        self.console.print(f"{prefix}[green]\u2713[/green] {message}")

    def warning(self, message: str, indent: bool = True) -> None:
        """Display a warning message with yellow exclamation.

        Args:
            message: Warning message.
            indent: Whether to indent the message.
        """
        prefix = "   " if indent else ""
        self.console.print(f"{prefix}[yellow]![/yellow] {message}")

    def error(self, message: str, indent: bool = True) -> None:
        """Display an error message with red X.

        Args:
            message: Error message.
            indent: Whether to indent the message.
        """
        prefix = "   " if indent else ""
        self.console.print(f"{prefix}[red]\u2717[/red] {message}")

    def detail(self, message: str, indent: bool = True) -> None:
        """Display secondary/dim information.

        Args:
            message: Detail message.
            indent: Whether to indent the message.
        """
        prefix = "   " if indent else ""
        self.console.print(f"{prefix}[dim]{message}[/dim]")

    def info(self, message: str, indent: bool = True) -> None:
        """Display an informational message.

        Args:
            message: Info message.
            indent: Whether to indent the message.
        """
        prefix = "   " if indent else ""
        self.console.print(f"{prefix}{message}")

    def progress(
        self,
        description: str = "Processing",
        total: int | None = None,
        transient: bool = True,
    ) -> Progress:
        """Create a Rich Progress instance for iteration.

        Args:
            description: Description shown next to the progress bar.
            total: Total number of items. None for indeterminate progress.
            transient: Whether to remove the progress bar when done.

        Returns:
            Rich Progress instance to use as context manager.
        """
        if total is None:
            # Indeterminate spinner
            return Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=self.console,
                transient=transient,
            )
        else:
            # Determinate progress bar
            return Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=self.console,
                transient=transient,
            )

    def validation_table(
        self,
        title: str,
        results: Sequence[dict[str, Any]],
    ) -> None:
        """Display a validation results table (for file checks).

        Args:
            title: Table title.
            results: Sequence of dicts with 'description', 'found', and optionally 'file'.
        """
        table = Table(title=title, show_header=False, box=None, padding=(0, 2))

        table.add_column("Status", justify="left", no_wrap=True)
        table.add_column("Description", style="white")

        for result in results:
            found = result.get("found", False)
            desc = result.get("description", "Unknown")

            if found:
                status = "[green]\u2713[/green]"
            else:
                status = "[red]\u2717[/red]"

            table.add_row(status, desc)

        self.console.print()
        self.console.print(table)


# Global console instance for convenience
_console: PapertrailConsole | None = None


def get_console() -> PapertrailConsole:
    """Get or create the global PapertrailConsole instance.

    Returns:
        The global PapertrailConsole instance.
    """
    global _console
    if _console is None:
        _console = PapertrailConsole()
    return _console
