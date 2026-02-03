"""
papertrail - AI-powered PDF document classification and organization.

Main CLI entry point for processing PDF documents.
"""

import os
import re
import json
import argparse
import sys
import tempfile
from pathlib import Path
from typing import Optional


def require_path(
    parser: argparse.ArgumentParser,
    path: Optional[str],
    name: str,
    must_exist: bool = True,
    must_be_dir: bool = True,
    create_if_missing: bool = False,
) -> Path:
    """Validate a CLI path argument.

    Args:
        parser: ArgumentParser to call error() on
        path: Path string to validate (may be None)
        name: Human-readable name for error messages (e.g., "processed_path")
        must_exist: If True, error if path doesn't exist
        must_be_dir: If True, error if path is not a directory
        create_if_missing: If True, create directory if it doesn't exist (requires must_be_dir)

    Returns:
        Path object if validation passes

    Raises:
        SystemExit via parser.error() if validation fails
    """
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

from papertrail.config import (
    get_config_paths,
    get_openai_client,
    set_current_profile,
)
from papertrail.profiles import (
    load_profile,
    list_available_profiles,
    get_profiles_dir,
    ProfileNotFoundError,
    ProfileError,
)
from papertrail.enums import reset_enum_cache
from papertrail.mappings import MappingsManager
from papertrail.rejected import RejectedValuesManager
from papertrail.nif_lookup import NIFLookupCache
from papertrail.logging_utils import setup_logging, get_logger, setup_task_logging

# Import all task functions
from papertrail.tasks import (
    task_extract_new,
    task_rename_files,
    task_reextract,
    task_validate_extraction,
    validate_metadata,
    export_metadata_to_excel,
    copy_matching_files,
    task_export_all_dates,
    check_files_exist,
    task_backfill_page_count,
    task_bootstrap_mappings,
    task_review_mappings,
    task_add_canonical,
    task_review_rejected,
    task_gmail_download,
    pipeline,
    task_qr_inventory,
)

# ------------------- LOGGING -------------------

logger = get_logger('cli')

# ------------------- CONFIG -------------------

from dataclasses import dataclass


@dataclass
class AppContext:
    """Runtime application context holding all initialized resources."""
    config_paths: dict
    model_id: str
    openai_client: any
    mappings_manager: any
    rejected_manager: any
    nif_cache: any  # NIFLookupCache or None


_ctx: AppContext | None = None


def get_ctx() -> AppContext:
    """Get the application context. Raises if not initialized."""
    if _ctx is None:
        raise RuntimeError("Application context not initialized. Call initialize_config() first.")
    return _ctx


def initialize_config(profile_name: Optional[str] = None) -> None:
    """Initialize configuration from profile."""
    global _ctx

    profiles_dir = get_profiles_dir()
    profiles_exist = profiles_dir.exists() and any(profiles_dir.glob("*.yaml"))

    if not profiles_exist:
        raise ProfileNotFoundError(
            "No profiles found. Create a profile from profiles/*.yaml.example. "
            "See profiles/README.md for documentation."
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
                "Use --profile to specify one, or create profiles/default.yaml."
            )

    if profile.profile.description:
        logger.info(f"  {profile.profile.description}")

    set_current_profile(profile)
    reset_enum_cache()

    config_dir = Path(__file__).parent / "config"
    mappings_path = config_dir / "mappings.yaml"
    rejected_path = config_dir / "rejected_values.yaml"
    nif_cache_path = config_dir / "nif_cache.yaml"

    # Initialize NIF cache if enabled
    nif_cache = None
    if profile.nif_api.enabled:
        nif_cache = NIFLookupCache(nif_cache_path)
        logger.info(f"NIF lookup enabled with {len(nif_cache)} cached entries")

    _ctx = AppContext(
        config_paths=get_config_paths(),
        model_id=profile.openrouter.model_id,
        openai_client=get_openai_client(),
        mappings_manager=MappingsManager(mappings_path),
        rejected_manager=RejectedValuesManager(rejected_path),
        nif_cache=nif_cache,
    )

    # When running as __main__, also register ourselves as 'main' module
    # so that `import main; main.get_ctx()` works from other modules
    if __name__ == "__main__":
        import sys
        # Register __main__ as 'main' so imports find us
        sys.modules["main"] = sys.modules["__main__"]


# ------------------- TASK DISPATCHER -------------------

def process_folder(task: str, processed_path: str, raw_paths=None, excel_output_path: str = None,
                   regex_pattern: str = None, copy_dest_folder: str = None, check_schema_path: str = None,
                   export_base_dir: str = None, run_merge: bool = False):
    """Dispatch to appropriate task handler."""
    if raw_paths is not None:
        raw_paths = [Path(p) for p in raw_paths]
    processed_path = Path(processed_path)
    processed_path.mkdir(parents=True, exist_ok=True)

    if task == "extract_new":
        task_extract_new(processed_path, raw_paths)
    elif task == "rename_files":
        task_rename_files(processed_path)
    elif task == "validate_metadata":
        setup_task_logging(processed_path, "validate_metadata")
        logger.info("Validating existing metadata and PDFs...")
        validate_metadata(processed_path)
        logger.info("Validation complete.")
    elif task == "export_excel":
        setup_task_logging(processed_path, "export_excel")
        logger.info("Exporting metadata to Excel...")
        export_metadata_to_excel(processed_path, excel_output_path)
        logger.info("Excel export complete.")
    elif task == "copy_matching":
        if not regex_pattern or not copy_dest_folder:
            logger.error("For 'copy_matching', --regex_pattern and --copy_dest_folder are required.")
            return
        setup_task_logging(processed_path, "copy_matching")
        stats = copy_matching_files(processed_path, regex_pattern, Path(copy_dest_folder))
        logger.info(f"Copied {stats['copied']} files matching '{regex_pattern}' to {copy_dest_folder}")
    elif task == "export_all_dates":
        task_export_all_dates(processed_path, Path(export_base_dir), run_merge)
    elif task == "check_files_exist":
        if not check_schema_path:
            logger.error("For 'check_files_exist', --check_schema_path is required.")
            return
        setup_task_logging(processed_path, "check_files_exist")
        check_files_exist(processed_path, Path(check_schema_path))
        logger.info("File existence check complete.")
    else:
        logger.error("Invalid task specified.")


# ------------------- MAIN -------------------

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Process a folder of PDF files.",
        epilog="Use --profile to select a configuration profile. "
               "Available profiles are listed in profiles/ directory."
    )
    parser.add_argument(
        "--profile",
        type=str,
        help="Configuration profile to use (e.g., 'default', 'personal', 'work'). "
             "If not specified, uses 'default' profile if available, otherwise legacy .env configuration."
    )
    parser.add_argument(
        "--env",
        type=str,
        default="bridge",
        help="Environment to use (loads .env.{name}). Default: bridge. "
             "Example: --env local loads .env.local for local LLM proxy."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose output with timestamps and debug messages."
    )
    parser.add_argument("task", type=str, choices=[
        'extract_new', 'rename_files', 'validate_metadata', 'export_excel',
        'copy_matching', 'export_all_dates', 'check_files_exist', 'pipeline',
        'gmail_download', 'bootstrap_mappings', 'review_mappings', 'add_canonical',
        'backfill_page_count', 'review_rejected', 'reextract', 'validate_extraction',
        'qr_inventory'
    ], help="Task to perform.")
    parser.add_argument("processed_path", type=str, nargs='?', help="Path to output folder.")
    parser.add_argument("--raw_path", type=str, help="Path to documents folder(s). Use ';' to separate multiple paths.")
    parser.add_argument("--excel_output_path", type=str, help="Path to output Excel file.")
    parser.add_argument("--regex_pattern", type=str, help="Regex pattern for matching filenames.")
    parser.add_argument("--copy_dest_folder", type=str, help="Destination folder for copied files.")
    parser.add_argument("--export_base_dir", type=str, help="Base export directory.")
    parser.add_argument("--run_merge", action="store_true", help="Run PDF merge for changed directories.")
    parser.add_argument("--check_schema_path", type=str, help="Validation schema path.")
    parser.add_argument("--export_date", type=str, help="Export date in YYYY-MM format (for pipeline).")
    parser.add_argument("--field", type=str, help="Field name for add_canonical (document_type or issuing_party).")
    parser.add_argument("--canonical", type=str, help="Canonical value to add.")
    parser.add_argument("--dry_run", action="store_true", help="Show what would be changed without modifying files (for reextract).")
    parser.add_argument("--all_unknown", action="store_true", help="Re-extract all files with $UNKNOWN$ values (for reextract).")
    parser.add_argument("--filename", type=str, help="Single filename to re-extract (for reextract).")
    parser.add_argument("--document_pattern", type=str, help="Glob pattern for matching files (for reextract/validate_extraction).")
    parser.add_argument("--export_path", type=str, help="Path to export folder for qr_inventory task.")
    parser.add_argument("--no_resume", action="store_true", help="Don't resume from checkpoint (for qr_inventory).")
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)

    try:
        initialize_config(args.profile)
    except ProfileNotFoundError as e:
        parser.error(str(e))
    except ProfileError as e:
        parser.error(f"Failed to load profile: {e}")

    if args.task == "pipeline":
        if args.export_date and not re.match(r"^\d{4}-\d{2}$", args.export_date):
            parser.error("The --export_date argument must be in YYYY-MM format.")
        pipeline(export_date_arg=args.export_date)
        return

    if args.task == "gmail_download":
        task_gmail_download()
        return

    if args.task == "review_mappings":
        task_review_mappings(get_ctx().mappings_manager)
        return

    if args.task == "review_rejected":
        task_review_rejected(get_ctx().rejected_manager, get_ctx().mappings_manager)
        return

    if args.task == "add_canonical":
        if not args.field or not args.canonical:
            parser.error("add_canonical requires --field and --canonical arguments.")
        task_add_canonical(get_ctx().mappings_manager, args.field, args.canonical)
        return

    if args.task == "bootstrap_mappings":
        processed = require_path(parser, args.processed_path, "processed_path")
        task_bootstrap_mappings(processed, get_ctx().mappings_manager)
        return

    if args.task == "backfill_page_count":
        processed = require_path(parser, args.processed_path, "processed_path")
        task_backfill_page_count(processed)
        return

    if args.task == "reextract":
        processed = require_path(parser, args.processed_path, "processed_path")
        if not (args.all_unknown or args.filename or args.document_pattern):
            parser.error("reextract requires at least one of: --all_unknown, --filename, --document_pattern")
        task_reextract(
            processed, dry_run=args.dry_run,
            all_unknown=args.all_unknown, filename=args.filename,
            document_pattern=args.document_pattern,
        )
        return

    if args.task == "validate_extraction":
        processed = require_path(parser, args.processed_path, "processed_path")
        task_validate_extraction(processed, document_pattern=args.document_pattern)
        return

    if args.task == "qr_inventory":
        # Determine export path: use --export_path if provided, else use profile export path
        export_path = args.export_path
        if not export_path:
            from papertrail.config import get_current_profile
            profile = get_current_profile()
            if profile and profile.paths.export:
                export_path = profile.paths.export
            else:
                parser.error("qr_inventory requires --export_path or export path in profile.")
        export = require_path(parser, export_path, "export_path")
        task_qr_inventory(export, resume=not args.no_resume)
        return

    processed_path = require_path(parser, args.processed_path, "processed_path")

    raw_paths = None
    if args.task == "extract_new":
        if not args.raw_path:
            parser.error("the --raw_path argument is required when task is 'extract_new'.")
        raw_path_strs = [p for p in args.raw_path.split(';') if p]
        if not raw_path_strs:
            parser.error("the --raw_path argument must contain at least one path.")
        raw_paths = [require_path(parser, rp, "raw_path") for rp in raw_path_strs]

    if args.task == "export_excel":
        if not args.excel_output_path:
            parser.error("the --excel_output_path argument is required when task is 'export_excel'.")
        if not args.excel_output_path.endswith(".xlsx"):
            parser.error("the --excel_output_path argument must end with '.xlsx'.")

    if args.task == "copy_matching":
        if not args.regex_pattern:
            parser.error("the --regex_pattern argument is required when task is 'copy_matching'.")
        require_path(parser, args.copy_dest_folder, "copy_dest_folder", create_if_missing=True)

    export_base_dir = args.export_base_dir
    if args.task == "export_all_dates":
        if not export_base_dir:
            export_base_dir = os.getenv("EXPORT_FILES_DIR")
        require_path(parser, export_base_dir, "export_base_dir", create_if_missing=True)

    check_schema_path = args.check_schema_path
    temp_check_schema_file = None
    if args.task == "check_files_exist":
        if not check_schema_path:
            from papertrail.config import get_validations
            validations, validations_file = get_validations()
            if validations and validations.get('rules'):
                if validations_file:
                    check_schema_path = validations_file
                else:
                    temp_check_schema = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
                    json.dump(validations['rules'], temp_check_schema, indent=2)
                    temp_check_schema.close()
                    check_schema_path = temp_check_schema.name
                    temp_check_schema_file = temp_check_schema.name
            else:
                parser.error("No validation rules found in profile. Use --check_schema_path or configure validations in profile.")
        require_path(parser, check_schema_path, "check_schema_path", must_be_dir=False)

    process_folder(
        args.task,
        str(processed_path),
        raw_paths=[str(p) for p in raw_paths] if raw_paths else None,
        excel_output_path=args.excel_output_path,
        regex_pattern=args.regex_pattern,
        copy_dest_folder=args.copy_dest_folder,
        export_base_dir=export_base_dir,
        run_merge=args.run_merge if hasattr(args, 'run_merge') else False,
        check_schema_path=check_schema_path if args.task == "check_files_exist" else args.check_schema_path
    )

    if temp_check_schema_file:
        try:
            os.unlink(temp_check_schema_file)
        except Exception:
            pass


if __name__ == "__main__":
    main()
