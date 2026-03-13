"""papertrail - AI-powered PDF document classification and organization."""

import os
import re
import sys
from pathlib import Path
from typing import Optional

import typer

from papertrail.app import create_app
from papertrail.config import ConfigError, ProfileNotFoundError
from papertrail.logging_utils import get_logger
from papertrail.workflow_utils import task_log_context
import papertrail.workflows as workflows

logger = get_logger("cli")

app = typer.Typer(
    help="AI-powered document classification and organization.",
    add_completion=False,
    invoke_without_command=True,
    context_settings={"token_normalize_func": lambda x: x.replace("_", "-")},
)
export_app = typer.Typer(help="Export documents and metadata.")
app.add_typer(export_app, name="export")


# ── Helpers ──────────────────────────────────────────────────────


def _fail(msg: str):
    typer.echo(f"Error: {msg}", err=True)
    raise typer.Exit(1)


def _profile_path(app, name: str) -> Optional[str]:
    """Get a named path ('processed', 'raw', 'export') from the current profile."""
    profile = app.profile if app else None
    if not profile:
        return None
    paths = profile.paths
    return {
        "processed": paths.processed,
        "raw": paths.raw[0] if paths.raw else None,
        "export": paths.export,
    }.get(name)


def _resolve_processed(app, processed_path: Optional[str] = None) -> Path:
    """Resolve processed_path from argument or profile, create if needed."""
    path_str = processed_path or _profile_path(app, "processed")
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


# ── Initialization ───────────────────────────────────────────────


def initialize_config(profile_name: Optional[str] = None) -> None:
    """Initialize configuration from profile."""
    create_app(profile_name=profile_name, verbose=False)


# ── App callback (global options + default command) ──────────────


@app.callback()
def main(
    ctx: typer.Context,
    profile: Optional[str] = typer.Option(None, help="Configuration profile to use."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """AI-powered document classification and organization."""
    # Skip heavy initialization when showing subcommand help
    if "--help" in sys.argv or "-h" in sys.argv:
        return
    try:
        app_instance = create_app(profile_name=profile, verbose=verbose)
    except (ProfileNotFoundError, ConfigError) as e:
        _fail(str(e))
    ctx.obj = {"app": app_instance}
    if ctx.invoked_subcommand is None:
        pipeline_cmd(ctx, months=2, export_date=None)


# ── Commands ─────────────────────────────────────────────────────


@app.command("pipeline")
def pipeline_cmd(
    ctx: typer.Context,
    months: int = typer.Option(2, help="Months to process (default: 2)."),
    export_date: Optional[str] = typer.Option(None, help="Export date in YYYY-MM format."),
):
    """Full end-to-end workflow (default)."""
    if months < 1:
        _fail("--months must be >= 1.")
    if export_date and not re.match(r"^\d{4}-\d{2}$", export_date):
        _fail("--export_date must be in YYYY-MM format.")
    workflows.pipeline(ctx.obj["app"], months=months, export_date_arg=export_date)


@app.command()
def extract(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    raw_path: Optional[str] = typer.Option(None, help="Document folder(s), ';'-separated."),
):
    """Process new PDFs/XLSX from raw folder."""
    app_instance = ctx.obj["app"]
    pp = _resolve_processed(app_instance, processed_path)
    if not raw_path:
        profile = app_instance.profile
        if profile and profile.paths.raw:
            raw_path = ";".join(profile.paths.raw)
    if not raw_path:
        _fail("--raw_path is required for extract.")
    raw_paths = [_resolve_dir(p, "raw_path") for p in raw_path.split(";") if p]
    if not raw_paths:
        _fail("--raw_path must contain at least one path.")
    workflows.extract(app_instance, pp, raw_paths)


@app.command()
def sync(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    pattern: Optional[str] = typer.Option(None, help="Glob/regex pattern."),
    dry_run: bool = typer.Option(False, help="Preview without modifying."),
    all_unknown: bool = typer.Option(False, help="Re-extract all $UNKNOWN$ values."),
    workers: int = typer.Option(1, "-w", "--workers", help="Parallel workers."),
    all_pdfs: bool = typer.Option(False, "--all", help="Process all PDFs, not just orphans."),
):
    """Sync metadata."""
    app_instance = ctx.obj["app"]
    workflows.sync(
        app_instance,
        _resolve_processed(app_instance, processed_path),
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
):
    """Reconcile bank transactions against documents."""
    app_instance = ctx.obj["app"]
    export = _resolve_dir(export_path or _profile_path(app_instance, "export"), "export_path")
    excel = Path(excel_path) if excel_path else None
    workflows.reconcile(app_instance, export, excel_path=excel, dry_run=dry_run)


@app.command()
def gmail(
    ctx: typer.Context,
    months: int = typer.Option(2, help="Months to process (default: 2)."),
):
    """Download email attachments from Gmail."""
    if months < 1:
        _fail("--months must be >= 1.")
    try:
        workflows.gmail(ctx.obj["app"], months=months)
    except RuntimeError:
        sys.exit(1)


@app.command()
def rename(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
):
    """Rename files based on metadata."""
    app_instance = ctx.obj["app"]
    workflows.rename(app_instance, _resolve_processed(app_instance, processed_path))


@app.command()
def check(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    verify_hashes: bool = typer.Option(
        False, "--verify-hashes", help="Verify file hashes match metadata."
    ),
    dry_run: bool = typer.Option(False, help="Report only, don't fix."),
):
    """Verify integrity, fill missing fields, audit report."""
    app_instance = ctx.obj["app"]
    workflows.check(
        app_instance,
        _resolve_processed(app_instance, processed_path),
        verify_hashes=verify_hashes,
        dry_run=dry_run,
    )


@app.command()
def archive(
    ctx: typer.Context,
    digest: list[str] = typer.Argument(help="One or more hash_file digests to archive."),
    processed_path: Optional[str] = typer.Option(None, help="Path to processed folder."),
    dry_run: bool = typer.Option(False, help="Preview without moving."),
):
    """Archive documents by hash digest."""
    app_instance = ctx.obj["app"]
    workflows.archive(app_instance, _resolve_processed(app_instance, processed_path), digest, dry_run=dry_run)


# ── Export subcommands ───────────────────────────────────────────


@export_app.command("excel")
def export_excel(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    output: str = typer.Option(..., help="Output .xlsx file path."),
):
    """Export metadata to Excel."""
    if not output.endswith(".xlsx"):
        _fail("--output must end with '.xlsx'.")
    app_instance = ctx.obj["app"]
    pp = _resolve_processed(app_instance, processed_path)
    with task_log_context(pp, "export_excel"):
        workflows.export_excel(app_instance, pp, output)


@export_app.command("dates")
def export_dates(
    ctx: typer.Context,
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    base_dir: Optional[str] = typer.Option(None, help="Base export directory."),
    run_merge: bool = typer.Option(False, help="Run PDF merge."),
):
    """Export files by date range."""
    app_instance = ctx.obj["app"]
    pp = _resolve_processed(app_instance, processed_path)
    profile = app_instance.profile
    export_base = (
        base_dir or (profile.paths.export if profile else None) or os.getenv("EXPORT_FILES_DIR")
    )
    export_dir = _resolve_dir(export_base, "base_dir", create=True)
    profile_context = None
    if profile and profile.profile.tax_number:
        profile_context = {"tax_number": profile.profile.tax_number}
    workflows.export_dates(
        app_instance,
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
):
    """Copy files matching pattern."""
    app_instance = ctx.obj["app"]
    pp = _resolve_processed(app_instance, processed_path)
    dest_path = _resolve_dir(dest, "dest", create=True)
    with task_log_context(pp, "copy_matching"):
        workflows.copy_matching(app_instance, pp, pattern, dest_path)


if __name__ == "__main__":
    app()
