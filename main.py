"""papertrail - AI-powered PDF document classification and organization."""

import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

import typer

from papertrail.config import ConfigError, ProfileNotFoundError
from papertrail.logging_utils import get_logger
from papertrail.runtime import Runtime, create_runtime
from papertrail import commands

logger = get_logger("cli")

app = typer.Typer(
    help="AI-powered document classification and organization.",
    add_completion=False,
    invoke_without_command=True,
    context_settings={"token_normalize_func": lambda x: x.replace("_", "-")},
)
export_app = typer.Typer(help="Export documents and metadata.")
app.add_typer(export_app, name="export")

PROFILE_OPTION_HELP = "Configuration profile to use. Required."


# ── Helpers ──────────────────────────────────────────────────────


def _fail(msg: str):
    typer.echo(f"Error: {msg}", err=True)
    raise typer.Exit(1)


def _resolve_runtime(
    ctx: typer.Context,
    *,
    profile: Optional[str] = None,
    verbose: bool = False,
) -> Runtime:
    """Create or reuse runtime using root or subcommand overrides."""
    state = dict(ctx.obj or {})
    selected_profile = profile or state.get("profile")
    selected_verbose = bool(state.get("verbose")) or verbose

    runtime = state.get("runtime")
    runtime_profile = state.get("runtime_profile")
    runtime_verbose = state.get("runtime_verbose")
    if runtime is None or runtime_profile != selected_profile or runtime_verbose != selected_verbose:
        try:
            runtime = create_runtime(profile_name=selected_profile, verbose=selected_verbose)
        except (ProfileNotFoundError, ConfigError) as e:
            _fail(str(e))
        state["runtime"] = runtime
        state["runtime_profile"] = selected_profile
        state["runtime_verbose"] = selected_verbose

    state["profile"] = selected_profile
    state["verbose"] = selected_verbose
    ctx.obj = state
    return runtime


def _profile_path(runtime: Runtime | None, name: str) -> Optional[str]:
    """Get a named path ('processed', 'raw', 'export') from the current profile."""
    profile = runtime.profile if runtime else None
    if not profile:
        return None
    paths = profile.paths
    return {
        "processed": paths.processed,
        "raw": paths.raw[0] if paths.raw else None,
        "export": paths.export,
    }.get(name)


def _resolve_processed(runtime: Runtime | None, processed_path: Optional[str] = None) -> Path:
    """Resolve processed_path from argument or profile, create if needed."""
    path_str = processed_path or _profile_path(runtime, "processed")
    if not path_str:
        _fail("processed_path is required.")
    p = Path(path_str)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _resolve_dir(path_str: Optional[str], name: str, create: bool = False) -> Path:
    """Validate a directory path exists, or fail."""
    if not path_str:
        _fail(f"the {name} argument is required.")
    p = Path(path_str)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    if not p.is_dir():
        _fail(f"'{path_str}' is not a valid directory.")
    return p


@contextmanager
def _task_log_context(runtime: Runtime, processed_path: Path, task_name: str):
    log_file_path = commands.setup_task_logging(processed_path, task_name)
    runtime.console.detail(f"Log: {log_file_path}", indent=False)
    yield log_file_path


def _run_pipeline(
    ctx: typer.Context,
    *,
    months: int,
    export_date: Optional[str],
    profile: Optional[str] = None,
    verbose: bool = False,
):
    if months < 1:
        _fail("--months must be >= 1.")
    if export_date and not re.match(r"^\d{4}-\d{2}$", export_date):
        _fail("--export_date must be in YYYY-MM format.")
    commands.pipeline(
        _resolve_runtime(ctx, profile=profile, verbose=verbose),
        months=months,
        export_date_arg=export_date,
    )


# ── App callback (global options + default command) ──────────────


@app.callback()
def main(
    ctx: typer.Context,
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """AI-powered document classification and organization."""
    # Skip heavy initialization when showing subcommand help
    if "--help" in sys.argv or "-h" in sys.argv:
        return
    ctx.obj = {"profile": profile, "verbose": verbose}
    if ctx.invoked_subcommand is None:
        runtime = _resolve_runtime(ctx)
        if not runtime.api_accessible:
            runtime.console.warning(
                "Skipping default pipeline because the LLM API is unavailable.",
                indent=False,
            )
            runtime.console.detail(
                "Run an offline subcommand explicitly, or retry once the API base URL is reachable.",
                indent=False,
            )
            typer.echo(ctx.get_help())
            return
        _run_pipeline(ctx, months=2, export_date=None, profile=profile, verbose=verbose)


# ── Commands ─────────────────────────────────────────────────────


@app.command("pipeline")
def pipeline_cmd(
    ctx: typer.Context,
    months: int = typer.Option(2, help="Months to process (default: 2)."),
    export_date: Optional[str] = typer.Option(None, help="Export date in YYYY-MM format."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Full end-to-end workflow (default)."""
    _run_pipeline(ctx, months=months, export_date=export_date, profile=profile, verbose=verbose)


@app.command()
def extract(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    raw_path: Optional[str] = typer.Option(None, help="Document folder(s), ';'-separated."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Process new PDFs/XLSX from raw folder."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    pp = _resolve_processed(runtime, processed_path)
    if not raw_path:
        profile = runtime.profile
        if profile and profile.paths.raw:
            raw_path = ";".join(profile.paths.raw)
    if not raw_path:
        _fail("--raw_path is required for extract.")
    raw_paths = [_resolve_dir(p, "raw_path") for p in raw_path.split(";") if p]
    if not raw_paths:
        _fail("--raw_path must contain at least one path.")
    commands.extract(runtime, pp, raw_paths)


@app.command()
def sync(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    pattern: Optional[str] = typer.Option(None, help="Glob/regex pattern."),
    dry_run: bool = typer.Option(False, help="Preview without modifying."),
    all_unknown: bool = typer.Option(False, help="Re-extract all $UNKNOWN$ values."),
    workers: int = typer.Option(1, "-w", "--workers", help="Parallel workers."),
    all_pdfs: bool = typer.Option(False, "--all", help="Process all PDFs, not just orphans."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Sync metadata."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    commands.sync(
        runtime,
        _resolve_processed(runtime, processed_path),
        dry_run=dry_run,
        all_unknown=all_unknown,
        pattern=pattern,
        workers=workers,
        all=all_pdfs,
    )


@app.command()
def reconcile(
    ctx: typer.Context,
    export_path: Optional[str] = typer.Option(None, help="Path to export folder."),
    excel_path: Optional[str] = typer.Option(None, help="Path to transactions Excel file."),
    dry_run: bool = typer.Option(False, help="Preview without modifying."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Reconcile bank transactions against documents."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    export = _resolve_dir(export_path or _profile_path(runtime, "export"), "export_path")
    excel = Path(excel_path) if excel_path else None
    commands.reconcile(runtime, export, excel_path=excel, dry_run=dry_run)


@app.command()
def review(
    ctx: typer.Context,
    export_path: Optional[str] = typer.Option(None, help="Path to export folder."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Refresh reconciliation data if needed, then launch the review UI."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    export = _resolve_dir(export_path or _profile_path(runtime, "export"), "export_path")
    commands.review(runtime, export)


@app.command()
def gmail(
    ctx: typer.Context,
    months: int = typer.Option(2, help="Months to process (default: 2)."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Download email attachments from Gmail."""
    if months < 1:
        _fail("--months must be >= 1.")
    try:
        commands.gmail(_resolve_runtime(ctx, profile=profile, verbose=verbose), months=months)
    except RuntimeError:
        sys.exit(1)


@app.command()
def rename(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Rename files based on metadata."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    commands.rename(runtime, _resolve_processed(runtime, processed_path))


@app.command()
def check(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    verify_hashes: bool = typer.Option(
        False, "--verify-hashes", help="Verify file hashes match metadata."
    ),
    dry_run: bool = typer.Option(False, help="Report only, don't fix."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Verify integrity, fill missing fields, audit report."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    commands.check(
        runtime,
        _resolve_processed(runtime, processed_path),
        verify_hashes=verify_hashes,
        dry_run=dry_run,
    )


@app.command()
def archive(
    ctx: typer.Context,
    digest: list[str] = typer.Argument(help="One or more hash_file digests to archive."),
    processed_path: Optional[str] = typer.Option(None, help="Path to processed folder."),
    dry_run: bool = typer.Option(False, help="Preview without moving."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Archive documents by hash digest."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    commands.archive(runtime, _resolve_processed(runtime, processed_path), digest, dry_run=dry_run)


# ── Export subcommands ───────────────────────────────────────────


@export_app.command("excel")
def export_excel(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    output: str = typer.Option(..., help="Output .xlsx file path."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Export metadata to Excel."""
    if not output.endswith(".xlsx"):
        _fail("--output must end with '.xlsx'.")
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    pp = _resolve_processed(runtime, processed_path)
    with _task_log_context(runtime, pp, "export_excel"):
        commands.export_excel(runtime, pp, output)


@export_app.command("dates")
def export_dates(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    base_dir: Optional[str] = typer.Option(None, help="Base export directory."),
    run_merge: bool = typer.Option(False, help="Run PDF merge."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Export files by date range."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    pp = _resolve_processed(runtime, processed_path)
    profile = runtime.profile
    export_base = (
        base_dir or (profile.paths.export if profile else None) or os.getenv("EXPORT_FILES_DIR")
    )
    export_dir = _resolve_dir(export_base, "base_dir", create=True)
    profile_context = None
    if profile and profile.profile.tax_number:
        profile_context = {"tax_number": profile.profile.tax_number}
    commands.export_dates(
        runtime,
        pp,
        export_dir,
        run_merge,
        export_config=profile.export if profile else None,
        profile_context=profile_context,
    )


@export_app.command("copy")
def export_copy(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    pattern: str = typer.Option(..., help="Pattern for matching files."),
    dest: str = typer.Option(..., help="Destination folder."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Copy files matching pattern."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    pp = _resolve_processed(runtime, processed_path)
    dest_path = _resolve_dir(dest, "dest", create=True)
    with _task_log_context(runtime, pp, "copy_matching"):
        commands.copy_matching(runtime, pp, pattern, dest_path)


if __name__ == "__main__":
    app()
