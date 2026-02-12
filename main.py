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
    task_validate_extraction,
    validate_metadata,
    export_metadata_to_excel,
    copy_matching_files,
    task_export_all_dates,
    task_backfill_page_count,
    task_backfill_file_size,
    task_backfill_text_hash,
    task_fix_unicode,
    task_gmail_download,
    pipeline,
    task_qr_inventory,
    task_reconcile,
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
        console.warning("LLM-dependent tasks will fail. Offline tasks (rename, validate, backfill) will work.", indent=False)

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


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Process a folder of PDF files.",
        epilog="Use --profile to select a configuration profile."
    )
    parser.add_argument("--profile", type=str, help="Configuration profile to use.")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output.")
    parser.add_argument("task", type=str, nargs='?', default='pipeline', choices=[
        'extract_new', 'rename_files', 'validate_metadata', 'export_excel',
        'copy_matching', 'export_all_dates', 'pipeline',
        'gmail_download', 'backfill_page_count', 'backfill_file_size',
        'backfill_text_hash', 'fix_unicode', 'sync', 'validate_extraction',
        'qr_inventory', 'reconcile',
    ], help="Task to perform (default: pipeline).")
    parser.add_argument("processed_path", type=str, nargs='?', help="Path to output folder.")
    parser.add_argument("--raw_path", type=str, help="Path to documents folder(s). Use ';' to separate multiple.")
    parser.add_argument("--excel_output_path", type=str, help="Path to output Excel file.")
    parser.add_argument("--pattern", type=str, help="Pattern for matching files (glob or regex).")
    parser.add_argument("--copy_dest_folder", type=str, help="Destination folder for copied files.")
    parser.add_argument("--export_base_dir", type=str, help="Base export directory.")
    parser.add_argument("--run_merge", action="store_true", help="Run PDF merge for changed directories.")
    parser.add_argument("--export_date", type=str, help="Export date in YYYY-MM format (for pipeline).")
    parser.add_argument("--dry_run", action="store_true", help="Show what would be changed without modifying files.")
    parser.add_argument("--all_unknown", action="store_true", help="Re-extract all files with $UNKNOWN$ values.")
    parser.add_argument("--workers", "-w", type=int, default=1, help="Number of parallel workers (default: 1).")
    parser.add_argument("--all", action="store_true", help="Process all matching PDFs, not just orphans.")
    parser.add_argument("--export_path", type=str, help="Path to export folder.")
    parser.add_argument("--excel_path", type=str, help="Path to transactions Excel file (for reconcile).")
    parser.add_argument("--no_resume", action="store_true", help="Don't resume from checkpoint.")
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    try:
        initialize_config(args.profile)
    except ProfileNotFoundError as e:
        parser.error(str(e))
    except ProfileError as e:
        parser.error(f"Failed to load profile: {e}")

    task = args.task

    if task == "pipeline":
        if args.export_date and not re.match(r"^\d{4}-\d{2}$", args.export_date):
            parser.error("The --export_date argument must be in YYYY-MM format.")
        pipeline(export_date_arg=args.export_date)
        return

    if task == "gmail_download":
        try:
            task_gmail_download()
        except RuntimeError:
            sys.exit(1)
        return

    if task == "qr_inventory":
        export = require_path(parser, args.export_path or _get_profile_path("export"), "export_path")
        task_qr_inventory(export, resume=not args.no_resume)
        return

    if task == "reconcile":
        export = require_path(parser, args.export_path or _get_profile_path("export"), "export_path")
        excel = Path(args.excel_path) if args.excel_path else None
        task_reconcile(export, excel_path=excel, dry_run=args.dry_run)
        return

    processed_path = require_path(parser, args.processed_path or _get_profile_path("processed"), "processed_path")
    processed_path.mkdir(parents=True, exist_ok=True)

    if task == "extract_new":
        raw_path_arg = args.raw_path
        if not raw_path_arg:
            from papertrail.config import get_current_profile
            profile = get_current_profile()
            if profile and profile.paths.raw:
                raw_path_arg = ";".join(profile.paths.raw)
        if not raw_path_arg:
            parser.error("the --raw_path argument is required when task is 'extract_new'.")
        raw_paths = [require_path(parser, rp, "raw_path") for rp in raw_path_arg.split(';') if rp]
        if not raw_paths:
            parser.error("the --raw_path argument must contain at least one path.")
        task_extract_new(processed_path, raw_paths)

    elif task == "rename_files":
        task_rename_files(processed_path)

    elif task == "validate_metadata":
        with task_log_context(processed_path, "validate_metadata"):
            validate_metadata(processed_path)

    elif task == "export_excel":
        if not args.excel_output_path:
            parser.error("the --excel_output_path argument is required when task is 'export_excel'.")
        if not args.excel_output_path.endswith(".xlsx"):
            parser.error("the --excel_output_path must end with '.xlsx'.")
        with task_log_context(processed_path, "export_excel"):
            export_metadata_to_excel(processed_path, args.excel_output_path)

    elif task == "copy_matching":
        if not args.pattern:
            parser.error("the --pattern argument is required when task is 'copy_matching'.")
        dest = require_path(parser, args.copy_dest_folder, "copy_dest_folder", create_if_missing=True)
        with task_log_context(processed_path, "copy_matching"):
            copy_matching_files(processed_path, args.pattern, dest)

    elif task == "export_all_dates":
        export_base_dir = args.export_base_dir or os.getenv("EXPORT_FILES_DIR")
        export_dir = require_path(parser, export_base_dir, "export_base_dir", create_if_missing=True)
        profile_context = None
        if profile.profile.tax_number:
            profile_context = {"tax_number": profile.profile.tax_number}
        task_export_all_dates(processed_path, export_dir, args.run_merge, export_config=profile.export, profile_context=profile_context)

    elif task == "sync":
        task_sync(
            processed_path, dry_run=args.dry_run,
            all_unknown=args.all_unknown, pattern=args.pattern,
            workers=args.workers, all=args.all,
        )

    elif task == "validate_extraction":
        task_validate_extraction(processed_path, pattern=args.pattern)

    elif task == "backfill_page_count":
        task_backfill_page_count(processed_path)

    elif task == "backfill_file_size":
        task_backfill_file_size(processed_path)

    elif task == "backfill_text_hash":
        task_backfill_text_hash(processed_path)

    elif task == "fix_unicode":
        task_fix_unicode(processed_path)


if __name__ == "__main__":
    main()
