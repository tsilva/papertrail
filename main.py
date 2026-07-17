"""papertrail - AI-powered PDF document classification and organization."""

import os
import re
import sys
from pathlib import Path
from typing import Optional

import typer

from papertrail import commands
from papertrail.config import ConfigError, ProfileNotFoundError
from papertrail.logging_utils import get_logger
from papertrail.runtime import Runtime, create_runtime

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


def _fail(msg: str):
    typer.echo(f"Error: {msg}", err=True)
    raise typer.Exit(1)


def _resolve_runtime(
    ctx: typer.Context,
    *,
    profile: Optional[str] = None,
    verbose: bool = False,
    enable_client: bool = True,
    probe_api: bool = True,
) -> Runtime:
    """Create a runtime using root or subcommand overrides."""
    state = ctx.obj or {}
    selected_profile = profile or state.get("profile")
    selected_verbose = bool(state.get("verbose")) or verbose

    try:
        return create_runtime(
            profile_name=selected_profile,
            verbose=selected_verbose,
            enable_client=enable_client,
            probe_api=probe_api,
        )
    except (ProfileNotFoundError, ConfigError) as e:
        _fail(str(e))


def _resolve_processed(runtime: Runtime, processed_path: Optional[str] = None) -> Path:
    """Resolve processed_path from argument or profile, create if needed."""
    path = Path(processed_path) if processed_path else runtime.paths.processed
    if path is None:
        _fail("processed_path is required.")
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_dir(path_value: str | Path | None, name: str, create: bool = False) -> Path:
    """Validate a directory path exists, or fail."""
    if not path_value:
        _fail(f"the {name} argument is required.")
    p = Path(path_value)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    if not p.is_dir():
        _fail(f"'{path_value}' is not a valid directory.")
    return p


def _run_pipeline(
    runtime: Runtime,
    *,
    months: int,
    export_date: Optional[str],
):
    if months < 1:
        _fail("--months must be >= 1.")
    if export_date and not re.match(r"^\d{4}-\d{2}$", export_date):
        _fail("--export_date must be in YYYY-MM format.")
    commands.pipeline(
        runtime,
        months=months,
        export_date_arg=export_date,
    )


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
                "Run an offline subcommand explicitly, or retry once the API base URL is "
                "reachable.",
                indent=False,
            )
            typer.echo(ctx.get_help())
            return
        _run_pipeline(
            runtime,
            months=int(runtime.profile.workflow.default_months or 2),
            export_date=None,
        )


@app.command("pipeline")
def pipeline_cmd(
    ctx: typer.Context,
    months: Optional[int] = typer.Option(
        None,
        help="Months to process. Defaults to workflow.default_months.",
    ),
    export_date: Optional[str] = typer.Option(None, help="Export date in YYYY-MM format."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Full end-to-end workflow (default)."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    _run_pipeline(
        runtime,
        months=months or int(runtime.profile.workflow.default_months or 2),
        export_date=export_date,
    )


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
        raw_path = ";".join(str(path) for path in runtime.paths.raw)
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
    workers: Optional[int] = typer.Option(
        None,
        "-w",
        "--workers",
        help="Parallel workers. Defaults to workflow.sync_workers.",
    ),
    all_pdfs: bool = typer.Option(False, "--all", help="Process all PDFs, not just orphans."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Sync metadata."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    resolved_workers = workers or int(runtime.profile.workflow.sync_workers or 1)
    commands.sync(
        runtime,
        _resolve_processed(runtime, processed_path),
        dry_run=dry_run,
        all_unknown=all_unknown,
        pattern=pattern,
        workers=resolved_workers,
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
    export = _resolve_dir(export_path or runtime.paths.export, "export_path")
    excel = Path(excel_path) if excel_path else None
    commands.reconcile(runtime, export, excel_path=excel, dry_run=dry_run)


@app.command()
def review(
    ctx: typer.Context,
    export_path: Optional[str] = typer.Option(None, help="Path to export folder."),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Launch the review UI for existing reconciliation sidecars."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    export = _resolve_dir(export_path or runtime.paths.export, "export_path")
    commands.review(runtime, export)


@app.command()
def regression(
    ctx: typer.Context,
    export_date: str = typer.Option(..., help="Export date in YYYY-MM format."),
    export_path: Optional[str] = typer.Option(None, help="Path to a month export folder."),
    seed_missing_approvals: bool = typer.Option(
        False,
        help="Seed missing approvals from existing successful reconciliation sidecars.",
    ),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Verify reconciliation output against approved groundtruth."""
    if not re.match(r"^\d{4}-\d{2}$", export_date):
        _fail("--export-date must be in YYYY-MM format.")
    runtime = _resolve_runtime(
        ctx,
        profile=profile,
        verbose=verbose,
        enable_client=False,
        probe_api=False,
    )
    export_root = Path(export_path) if export_path else runtime.paths.export
    if not export_root:
        _fail("export_path is required.")
    export_base = Path(export_root)
    export = export_base if export_path else export_base / export_date
    if not export.is_dir():
        _fail(f"'{export}' is not a valid export month directory.")

    from papertrail.reconciliation_regression import verify_reconciliation_regression
    from papertrail.repository import DocumentRepository

    result = verify_reconciliation_regression(
        runtime,
        DocumentRepository(runtime),
        export,
        seed_missing=seed_missing_approvals,
    )
    if result.ok:
        runtime.console.success(
            f"Regression passed: {result.checked} approved transaction(s) checked",
            indent=False,
        )
        return

    for failure in result.failures:
        runtime.console.error(failure, indent=False)
    _fail(f"Regression failed with {len(result.failures)} issue(s).")


@app.command()
def gmail(
    ctx: typer.Context,
    months: Optional[int] = typer.Option(
        None,
        help="Months to process. Defaults to gmail.default_months.",
    ),
    profile: Optional[str] = typer.Option(None, help=PROFILE_OPTION_HELP),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """Download email attachments from Gmail."""
    runtime = _resolve_runtime(ctx, profile=profile, verbose=verbose)
    resolved_months = months or int(runtime.profile.gmail.default_months or 2)
    if resolved_months < 1:
        _fail("--months must be >= 1.")
    try:
        commands.gmail(runtime, months=resolved_months)
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
    with commands.task_log_context(runtime, pp, "export_excel"):
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
    export_base = base_dir or runtime.paths.export or os.getenv("EXPORT_FILES_DIR")
    export_dir = _resolve_dir(export_base, "base_dir", create=True)
    commands.export_dates(runtime, pp, export_dir, run_merge)


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
    with commands.task_log_context(runtime, pp, "copy_matching"):
        commands.copy_matching(runtime, pp, pattern, dest_path)


if __name__ == "__main__":
    app()
