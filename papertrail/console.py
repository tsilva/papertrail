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
    status: str = "pending"
    message: str = ""


class StepContext:
    """Allows marking a step as success/warning/error during execution."""

    def __init__(self) -> None:
        self._result = StepResult()

    def success(self, message: str = "") -> None:
        self._result.status = "success"
        self._result.message = message

    def warning(self, message: str = "") -> None:
        self._result.status = "warning"
        self._result.message = message

    def error(self, message: str = "") -> None:
        self._result.status = "error"
        self._result.message = message

    @property
    def result(self) -> StepResult:
        return self._result


class PapertrailConsole:
    """Rich-based console for papertrail CLI output."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console()
        self._step_counter = 0
        self._task_name: str | None = None

    def pipeline_header(self, profile_name: str, log_path: str | None = None) -> None:
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
        """Context manager for a task with header/footer."""
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
        if number is None:
            self._step_counter += 1
            number = self._step_counter

        self.console.print(f"[cyan]{number}.[/cyan] {message}")

    @contextmanager
    def step_progress(self, message: str) -> Generator[StepContext, None, None]:
        """Step with spinner that shows result symbol on completion."""
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
        prefix = "   " if indent else ""
        self.console.print(f"{prefix}[green]\u2713[/green] {message}")

    def warning(self, message: str, indent: bool = True) -> None:
        prefix = "   " if indent else ""
        self.console.print(f"{prefix}[yellow]![/yellow] {message}")

    def error(self, message: str, indent: bool = True) -> None:
        prefix = "   " if indent else ""
        self.console.print(f"{prefix}[red]\u2717[/red] {message}")

    def detail(self, message: str, indent: bool = True) -> None:
        prefix = "   " if indent else ""
        self.console.print(f"{prefix}[dim]{message}[/dim]")

    def info(self, message: str, indent: bool = True) -> None:
        prefix = "   " if indent else ""
        self.console.print(f"{prefix}{message}")

    def progress(
        self,
        description: str = "Processing",
        total: int | None = None,
        transient: bool = True,
    ) -> Progress:
        """Create a Rich Progress instance."""
        if total is None:
            return Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TimeElapsedColumn(),
                console=self.console,
                transient=transient,
            )
        else:
            return Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                MofNCompleteColumn(),
                TimeElapsedColumn(),
                console=self.console,
                transient=transient,
            )

    def track(
        self,
        items,
        description: str = "Processing",
        transient: bool = True,
    ) -> Generator[Any, None, None]:
        """Iterate over items with automatic progress bar."""
        items = list(items)
        with self.progress(description, total=len(items), transient=transient) as progress:
            task_id = progress.add_task(description, total=len(items))
            for item in items:
                yield item
                progress.update(task_id, advance=1)

    def validation_table(
        self,
        title: str,
        results: Sequence[dict[str, Any]],
    ) -> None:
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


_console: PapertrailConsole | None = None


def get_console() -> PapertrailConsole:
    global _console
    if _console is None:
        _console = PapertrailConsole()
    return _console
