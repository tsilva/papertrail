"""papertrail - AI-powered PDF document classification and organization."""

import os
import re
import sys
from pathlib import Path
from typing import Optional

import typer

from papertrail.config import (
    AppContext,
    ConfigError,
    ProfileNotFoundError,
    get_cache_dir,
    get_openai_client,
    get_profiles_dir,
    list_available_profiles,
    load_profile,
    set_current_profile,
    set_ctx,
)
from papertrail.models import reset_enum_cache, reset_session_cache
from papertrail.nif_lookup import NIFLookupCache
from papertrail.logging_utils import setup_logging, get_logger
from papertrail.console import get_console

from papertrail.tasks import (
    task_extract_new,
    task_rename_files,
    task_sync,
    export_metadata_to_excel,
    copy_matching_files,
    task_export_all_dates,
    task_gmail_download,
    pipeline,
    task_reconcile,
    task_check,
    task_archive,
    task_log_context,
)

logger = get_logger('cli')

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


def _profile_path(name: str) -> Optional[str]:
    """Get a named path ('processed', 'raw', 'export') from the current profile."""
    from papertrail.config import get_current_profile
    profile = get_current_profile()
    if not profile:
        return None
    paths = profile.paths
    return {"processed": paths.processed, "raw": paths.raw[0] if paths.raw else None, "export": paths.export}.get(name)


def _resolve_processed(processed_path: Optional[str] = None) -> Path:
    """Resolve processed_path from argument or profile, create if needed."""
    path_str = processed_path or _profile_path("processed")
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
    profiles_dir = get_profiles_dir()
    if os.environ.get("PAPERTRAIL_PROFILES_DIR"):
        logger.info(f"Using external profiles directory: {profiles_dir}")

    if not (profiles_dir.exists() and list_available_profiles()):
        _fail(
            "No profiles found. Create a profile directory (e.g. profiles/default/) "
            "with a profile.yaml file. See profiles/README.md for documentation."
        )

    if profile_name is not None:
        profile = load_profile(profile_name)
        logger.info(f"Using profile: {profile.profile.name}")
    else:
        available = list_available_profiles()
        if "default" in available:
            profile = load_profile("default")
            logger.info(f"Using profile: {profile.profile.name} (auto-detected)")
        else:
            _fail(
                f"No 'default' profile found. Available profiles: {', '.join(available)}. "
                "Use --profile to specify one, or create profiles/default/profile.yaml."
            )

    if profile.profile.description:
        logger.info(f"  {profile.profile.description}")

    set_current_profile(profile)
    reset_enum_cache()
    reset_session_cache()

    from papertrail.config import check_api_accessibility
    base_url = profile.openrouter.base_url
    api_accessible = check_api_accessibility(base_url)
    if not api_accessible:
        console = get_console()
        console.warning(f"API base URL is not accessible: {base_url}", indent=False)
        console.warning("LLM-dependent tasks will fail. Offline tasks (rename, check, export) will work.", indent=False)
        from rich.prompt import Confirm
        if not Confirm.ask("Proceed without API access?", default=True):
            raise SystemExit(0)

    cache_dir = get_cache_dir()
    nif_cache = None
    if profile.nif_api.enabled:
        nif_cache = NIFLookupCache(cache_dir / "nif_cache.yaml")
        logger.info(f"NIF lookup enabled with {len(nif_cache)} cached entries")

    set_ctx(AppContext(
        model_id=profile.openrouter.model_id,
        openai_client=get_openai_client() if api_accessible else None,
        nif_cache=nif_cache,
    ))


# ── App callback (global options + default command) ──────────────

@app.callback()
def main(
    ctx: typer.Context,
    profile: Optional[str] = typer.Option(None, help="Configuration profile to use."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Enable verbose output."),
):
    """AI-powered document classification and organization."""
    setup_logging(verbose=verbose)
    # Skip heavy initialization when showing subcommand help
    if "--help" in sys.argv or "-h" in sys.argv:
        return
    try:
        initialize_config(profile)
    except (ProfileNotFoundError, ConfigError) as e:
        _fail(str(e))
    if ctx.invoked_subcommand is None:
        pipeline_cmd(months=2, export_date=None)


# ── Commands ─────────────────────────────────────────────────────

@app.command("pipeline")
def pipeline_cmd(
    months: int = typer.Option(2, help="Months to process (default: 2)."),
    export_date: Optional[str] = typer.Option(None, help="Export date in YYYY-MM format."),
):
    """Full end-to-end workflow (default)."""
    if months < 1:
        _fail("--months must be >= 1.")
    if export_date and not re.match(r"^\d{4}-\d{2}$", export_date):
        _fail("--export_date must be in YYYY-MM format.")
    pipeline(months=months, export_date_arg=export_date)


@app.command()
def extract(
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    raw_path: Optional[str] = typer.Option(None, help="Document folder(s), ';'-separated."),
):
    """Process new PDFs/XLSX from raw folder."""
    pp = _resolve_processed(processed_path)
    if not raw_path:
        from papertrail.config import get_current_profile
        profile = get_current_profile()
        if profile and profile.paths.raw:
            raw_path = ";".join(profile.paths.raw)
    if not raw_path:
        _fail("--raw_path is required for extract.")
    raw_paths = [_resolve_dir(p, "raw_path") for p in raw_path.split(";") if p]
    if not raw_paths:
        _fail("--raw_path must contain at least one path.")
    task_extract_new(pp, raw_paths)


@app.command()
def sync(
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    pattern: Optional[str] = typer.Option(None, help="Glob/regex pattern."),
    dry_run: bool = typer.Option(False, help="Preview without modifying."),
    all_unknown: bool = typer.Option(False, help="Re-extract all $UNKNOWN$ values."),
    workers: int = typer.Option(1, "-w", "--workers", help="Parallel workers."),
    all_pdfs: bool = typer.Option(False, "--all", help="Process all PDFs, not just orphans."),
):
    """Sync metadata."""
    task_sync(
        _resolve_processed(processed_path),
        dry_run=dry_run, all_unknown=all_unknown,
        pattern=pattern, workers=workers, all=all_pdfs,
    )


@app.command()
def reconcile(
    export_path: Optional[str] = typer.Option(None, help="Path to export folder."),
    excel_path: Optional[str] = typer.Option(None, help="Path to transactions Excel file."),
    dry_run: bool = typer.Option(False, help="Preview without modifying."),
):
    """Reconcile bank transactions against documents."""
    export = _resolve_dir(export_path or _profile_path("export"), "export_path")
    excel = Path(excel_path) if excel_path else None
    task_reconcile(export, excel_path=excel, dry_run=dry_run)


@app.command()
def gmail(
    months: int = typer.Option(2, help="Months to process (default: 2)."),
):
    """Download email attachments from Gmail."""
    if months < 1:
        _fail("--months must be >= 1.")
    try:
        task_gmail_download(months=months)
    except RuntimeError:
        sys.exit(1)


@app.command()
def rename(
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
):
    """Rename files based on metadata."""
    task_rename_files(_resolve_processed(processed_path))


@app.command()
def check(
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    verify_hashes: bool = typer.Option(False, "--verify-hashes", help="Verify file hashes match metadata."),
    dry_run: bool = typer.Option(False, help="Report only, don't fix."),
):
    """Verify integrity, fill missing fields, audit report."""
    task_check(_resolve_processed(processed_path), verify_hashes=verify_hashes, dry_run=dry_run)


@app.command()
def archive(
    digest: list[str] = typer.Argument(help="One or more hash_file digests to archive."),
    processed_path: Optional[str] = typer.Option(None, help="Path to processed folder."),
    dry_run: bool = typer.Option(False, help="Preview without moving."),
):
    """Archive documents by hash digest."""
    task_archive(_resolve_processed(processed_path), digest, dry_run=dry_run)


# ── Export subcommands ───────────────────────────────────────────

@export_app.command("excel")
def export_excel(
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    output: str = typer.Option(..., help="Output .xlsx file path."),
):
    """Export metadata to Excel."""
    if not output.endswith(".xlsx"):
        _fail("--output must end with '.xlsx'.")
    pp = _resolve_processed(processed_path)
    with task_log_context(pp, "export_excel"):
        export_metadata_to_excel(pp, output)


@export_app.command("dates")
def export_dates(
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    base_dir: Optional[str] = typer.Option(None, help="Base export directory."),
    run_merge: bool = typer.Option(False, help="Run PDF merge."),
):
    """Export files by date range."""
    pp = _resolve_processed(processed_path)
    from papertrail.config import get_current_profile
    profile = get_current_profile()
    export_base = base_dir or (profile.paths.export if profile else None) or os.getenv("EXPORT_FILES_DIR")
    export_dir = _resolve_dir(export_base, "base_dir", create=True)
    profile_context = None
    if profile and profile.profile.tax_number:
        profile_context = {"tax_number": profile.profile.tax_number}
    task_export_all_dates(
        pp, export_dir, run_merge,
        export_config=profile.export if profile else None,
        profile_context=profile_context,
    )


@export_app.command("copy")
def export_copy(
    processed_path: Optional[str] = typer.Argument(None, help="Path to processed folder."),
    pattern: str = typer.Option(..., help="Pattern for matching files."),
    dest: str = typer.Option(..., help="Destination folder."),
):
    """Copy files matching pattern."""
    pp = _resolve_processed(processed_path)
    dest_path = _resolve_dir(dest, "dest", create=True)
    with task_log_context(pp, "copy_matching"):
        copy_matching_files(pp, pattern, dest_path)


if __name__ == "__main__":
    app()
