"""papertrail - AI-powered PDF document classification and organization."""

import os
import re
import argparse
import sys
from pathlib import Path
from typing import Optional

from papertrail.config import (
    AppContext,
    get_cache_dir,
    get_openai_client,
    set_current_profile,
    set_ctx,
)
from papertrail.profiles import (
    load_profile,
    list_available_profiles,
    get_profiles_dir,
    ProfileNotFoundError,
    ProfileError,
)
from papertrail.enums import reset_enum_cache, reset_session_cache
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


def require_path(
    parser: argparse.ArgumentParser,
    path: Optional[str],
    name: str,
    must_exist: bool = True,
    must_be_dir: bool = True,
    create_if_missing: bool = False,
) -> Path:
    """Validate and return a CLI path argument, or call parser.error()."""
    if not path:
        parser.error(f"the {name} argument is required.")
    p = Path(path)
    if create_if_missing and must_be_dir:
        p.mkdir(parents=True, exist_ok=True)
    if must_exist and not p.exists():
        parser.error(f"The {name} '{path}' does not exist.")
    if must_be_dir and p.exists() and not p.is_dir():
        parser.error(f"The {name} '{path}' is not a directory.")
    return p


def _get_profile_path(path_name: str) -> Optional[str]:
    """Get a named path ('processed', 'raw', 'export') from the current profile."""
    from papertrail.config import get_current_profile
    profile = get_current_profile()
    if not profile:
        return None
    paths = profile.paths
    return {"processed": paths.processed, "raw": paths.raw[0] if paths.raw else None, "export": paths.export}.get(path_name)


def initialize_config(profile_name: Optional[str] = None) -> None:
    """Initialize configuration from profile."""
    profiles_dir = get_profiles_dir()
    if os.environ.get("PAPERTRAIL_PROFILES_DIR"):
        logger.info(f"Using external profiles directory: {profiles_dir}")

    if not (profiles_dir.exists() and list_available_profiles()):
        raise ProfileNotFoundError(
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
            raise ProfileNotFoundError(
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


def get_processed_path(args, parser) -> Path:
    """Resolve processed_path from args or profile, with validation."""
    path_str = getattr(args, 'processed_path', None) or _get_profile_path("processed")
    p = require_path(parser, path_str, "processed_path")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _add_processed_path(sub):
    """Add optional processed_path positional to a subparser."""
    sub.add_argument("processed_path", nargs="?", help="Path to processed folder (default: from profile).")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="AI-powered document classification and organization.",
        epilog="Use --profile to select a configuration profile."
    )
    parser.add_argument("--profile", type=str, help="Configuration profile to use.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output.")

    sub = parser.add_subparsers(dest="command", title="commands")

    # pipeline (default when no command given)
    p = sub.add_parser("pipeline", help="Full end-to-end workflow (default).")
    grp = p.add_mutually_exclusive_group()
    grp.add_argument("--months", type=int, default=2, help="Months to process (default: 2).")
    grp.add_argument("--export_date", type=str, help="Export date in YYYY-MM format.")

    # extract (was extract_new)
    p = sub.add_parser("extract", help="Process new PDFs/XLSX from raw folder.")
    _add_processed_path(p)
    p.add_argument("--raw_path", type=str, help="Document folder(s), ';'-separated.")

    # sync
    p = sub.add_parser("sync", help="Sync metadata.")
    _add_processed_path(p)
    p.add_argument("--pattern", type=str, help="Glob/regex pattern.")
    p.add_argument("--dry_run", action="store_true", help="Preview without modifying.")
    p.add_argument("--all_unknown", action="store_true", help="Re-extract all $UNKNOWN$ values.")
    p.add_argument("--workers", "-w", type=int, default=1, help="Parallel workers (default: 1).")
    p.add_argument("--all", action="store_true", help="Process all PDFs, not just orphans.")

    # reconcile
    p = sub.add_parser("reconcile", help="Reconcile bank transactions against documents.")
    p.add_argument("--export_path", type=str, help="Path to export folder.")
    p.add_argument("--excel_path", type=str, help="Path to transactions Excel file.")
    p.add_argument("--dry_run", action="store_true", help="Preview without modifying.")

    # gmail
    p = sub.add_parser("gmail", help="Download email attachments from Gmail.")
    p.add_argument("--months", type=int, default=2, help="Months to process (default: 2).")

    # rename
    p = sub.add_parser("rename", help="Rename files based on metadata.")
    _add_processed_path(p)

    # check (unified: backfill + audit)
    p = sub.add_parser("check", help="Verify integrity, fill missing fields, audit report.")
    _add_processed_path(p)
    p.add_argument("--verify-hashes", action="store_true",
                   help="Verify file hashes match metadata (expensive).")
    p.add_argument("--dry_run", action="store_true", help="Report only, don't fix.")

    # archive
    p = sub.add_parser("archive", help="Archive documents by hash digest.")
    p.add_argument("digest", nargs="+", help="One or more hash_file digests to archive.")
    _add_processed_path(p)
    p.add_argument("--dry_run", action="store_true", help="Preview without moving.")

    # export (subcommand group)
    p_export = sub.add_parser("export", help="Export documents and metadata.")
    esub = p_export.add_subparsers(dest="export_command", title="export tasks")

    ep = esub.add_parser("excel", help="Export metadata to Excel.")
    _add_processed_path(ep)
    ep.add_argument("--output", type=str, required=True, help="Output .xlsx file path.")

    ep = esub.add_parser("dates", help="Export files by date range.")
    _add_processed_path(ep)
    ep.add_argument("--base_dir", type=str, help="Base export directory.")
    ep.add_argument("--run_merge", action="store_true", help="Run PDF merge.")

    ep = esub.add_parser("copy", help="Copy files matching pattern.")
    _add_processed_path(ep)
    ep.add_argument("--pattern", type=str, required=True, help="Pattern for matching files.")
    ep.add_argument("--dest", type=str, required=True, help="Destination folder.")

    args = parser.parse_args()

    # Default to pipeline when no command given
    if not args.command:
        args.command = "pipeline"

    setup_logging(verbose=args.verbose)

    try:
        initialize_config(args.profile)
    except ProfileNotFoundError as e:
        parser.error(str(e))
    except ProfileError as e:
        parser.error(f"Failed to load profile: {e}")

    cmd = args.command

    # --- Dispatch ---

    if cmd == "pipeline":
        months = getattr(args, 'months', 2) or 2
        export_date = getattr(args, 'export_date', None)
        if months < 1:
            parser.error("--months must be >= 1.")
        if export_date and not re.match(r"^\d{4}-\d{2}$", export_date):
            parser.error("--export_date must be in YYYY-MM format.")
        pipeline(months=months, export_date_arg=export_date)

    elif cmd == "extract":
        processed_path = get_processed_path(args, parser)
        raw_path_arg = args.raw_path
        if not raw_path_arg:
            from papertrail.config import get_current_profile
            profile = get_current_profile()
            if profile and profile.paths.raw:
                raw_path_arg = ";".join(profile.paths.raw)
        if not raw_path_arg:
            parser.error("--raw_path is required for extract.")
        raw_paths = [require_path(parser, rp, "raw_path") for rp in raw_path_arg.split(';') if rp]
        if not raw_paths:
            parser.error("--raw_path must contain at least one path.")
        task_extract_new(processed_path, raw_paths)

    elif cmd == "sync":
        processed_path = get_processed_path(args, parser)
        task_sync(
            processed_path, dry_run=args.dry_run,
            all_unknown=args.all_unknown, pattern=args.pattern,
            workers=args.workers, all=args.all,
        )

    elif cmd == "reconcile":
        export = require_path(parser, args.export_path or _get_profile_path("export"), "export_path")
        excel = Path(args.excel_path) if args.excel_path else None
        task_reconcile(export, excel_path=excel, dry_run=args.dry_run)

    elif cmd == "gmail":
        if args.months < 1:
            parser.error("--months must be >= 1.")
        try:
            task_gmail_download(months=args.months)
        except RuntimeError:
            sys.exit(1)

    elif cmd == "rename":
        processed_path = get_processed_path(args, parser)
        task_rename_files(processed_path)

    elif cmd == "check":
        processed_path = get_processed_path(args, parser)
        task_check(processed_path, verify_hashes=args.verify_hashes, dry_run=args.dry_run)

    elif cmd == "archive":
        processed_path = get_processed_path(args, parser)
        task_archive(processed_path, args.digest, dry_run=args.dry_run)

    elif cmd == "export":
        if not args.export_command:
            p_export.print_help()
            sys.exit(1)
        ecmd = args.export_command
        if ecmd == "excel":
            processed_path = get_processed_path(args, parser)
            if not args.output.endswith(".xlsx"):
                parser.error("--output must end with '.xlsx'.")
            with task_log_context(processed_path, "export_excel"):
                export_metadata_to_excel(processed_path, args.output)
        elif ecmd == "dates":
            processed_path = get_processed_path(args, parser)
            from papertrail.config import get_current_profile
            profile = get_current_profile()
            export_base_dir = getattr(args, 'base_dir', None) or (profile.paths.export if profile else None) or os.getenv("EXPORT_FILES_DIR")
            export_dir = require_path(parser, export_base_dir, "base_dir", create_if_missing=True)
            profile_context = None
            if profile and profile.profile.tax_number:
                profile_context = {"tax_number": profile.profile.tax_number}
            task_export_all_dates(processed_path, export_dir, args.run_merge, export_config=profile.export if profile else None, profile_context=profile_context)
        elif ecmd == "copy":
            processed_path = get_processed_path(args, parser)
            dest = require_path(parser, args.dest, "dest", create_if_missing=True)
            with task_log_context(processed_path, "copy_matching"):
                copy_matching_files(processed_path, args.pattern, dest)



if __name__ == "__main__":
    main()
