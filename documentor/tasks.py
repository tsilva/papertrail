"""Task handlers for CLI commands.

Each task handler implements a specific CLI command like extract_new, rename_files, etc.
These functions are called from main.py and receive all necessary dependencies as parameters.
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from tqdm import tqdm

from documentor.config import get_current_profile
from documentor.hashing import hash_file_fast, hash_file_content
from documentor.logging_utils import setup_failure_logger
from documentor.metadata import build_hash_index, get_unique_dates, iter_json_files
from documentor.models import DocumentMetadata
from documentor.pdf import find_pdf_files


# ============================================================================
# Task Handlers
# ============================================================================


def task_extract_new(
    processed_path: Path,
    raw_paths: list[Path],
    rename_pdf_files_fn: Callable,
):
    """Extract and classify new PDF files.

    Args:
        processed_path: Directory for processed files
        raw_paths: List of directories containing raw PDF files
        rename_pdf_files_fn: Function to rename and process PDF files
    """
    log_path = processed_path / "classification_failures.log"
    failure_logger = setup_failure_logger(log_path)
    print(f"Logging failures to: {log_path}")

    print("Building hash index from metadata files...")
    known_hashes = set(build_hash_index(processed_path).keys())

    print("Scanning for new PDFs...")
    pdf_paths = find_pdf_files(raw_paths)
    print(f"Found {len(pdf_paths)} PDFs in raw directories")

    print("Stage 1: Quick filtering using fast file hashes...")
    fast_hash_map = {pdf: hash_file_fast(pdf) for pdf in tqdm(pdf_paths, desc="Fast hashing")}
    potentially_new = [pdf for pdf in pdf_paths if fast_hash_map[pdf] not in known_hashes]

    already_processed = len(pdf_paths) - len(potentially_new)
    print(f"  -> Skipped {already_processed} already-processed files")
    print(f"  -> {len(potentially_new)} files need content-based hashing")

    if not potentially_new:
        print("No new PDFs to process.")
        return

    print(f"Stage 2: Content-based hashing for {len(potentially_new)} new files...")
    content_hash_map = {}

    for pdf in tqdm(potentially_new, desc="Content hashing"):
        try:
            content_hash = hash_file_content(pdf)
            content_hash_map[pdf] = content_hash
        except Exception as e:
            print(f"\n  Error hashing {pdf.name}: {e}")

    files_to_process = [pdf for pdf in potentially_new if content_hash_map.get(pdf) not in known_hashes]
    print(f"Found {len(files_to_process)} truly new PDFs to process.")

    if files_to_process:
        rename_pdf_files_fn(files_to_process, content_hash_map, known_hashes, processed_path)

    print("Extraction complete.")


def task_rename_files(
    processed_path: Path,
    file_name_from_metadata_fn: Callable,
):
    """Rename existing PDF files based on metadata.

    Args:
        processed_path: Directory containing processed files
        file_name_from_metadata_fn: Function to generate filename from metadata
    """
    print("Renaming existing PDF files and metadata based on metadata...")

    valid_entries = []

    for metadata_path, metadata in iter_json_files(processed_path, show_progress=True, progress_desc="Validating metadata", validate=True):
        pdf_path = metadata_path.with_suffix(".pdf")
        if not pdf_path.exists():
            print(f"Skipping {metadata_path.name}: PDF file not found")
            continue

        valid_entries.append((pdf_path, metadata))

    print(f"Found {len(valid_entries)} files to rename")

    renamed_count = 0
    for old_pdf_path, metadata in valid_entries:
        content_hash = metadata.content_hash
        new_filename = file_name_from_metadata_fn(metadata, content_hash)
        new_pdf_path = processed_path / new_filename
        new_metadata_path = new_pdf_path.with_suffix(".json")

        if old_pdf_path == new_pdf_path:
            continue

        try:
            old_metadata_path = old_pdf_path.with_suffix(".json")
            shutil.move(old_pdf_path, new_pdf_path)
            shutil.move(old_metadata_path, new_metadata_path)
            renamed_count += 1
            if renamed_count <= 10 or renamed_count % 100 == 0:
                print(f"[{renamed_count}] Renamed: {old_pdf_path.name} -> {new_filename}")
        except Exception as e:
            print(f"Failed to rename {old_pdf_path.name}: {e}")

    print(f"Renaming complete. Renamed {renamed_count} files.")


def task_validate_metadata(
    processed_path: Path,
    validate_metadata_fn: Callable,
):
    """Validate metadata and PDFs.

    Args:
        processed_path: Directory containing processed files
        validate_metadata_fn: Function to validate metadata files
    """
    print("Validating existing metadata and PDFs...")
    validate_metadata_fn(processed_path)
    print("Validation complete.")


def task_export_excel(
    processed_path: Path,
    excel_output_path: str,
    export_metadata_to_excel_fn: Callable,
):
    """Export metadata to Excel.

    Args:
        processed_path: Directory containing processed files
        excel_output_path: Path for Excel output file
        export_metadata_to_excel_fn: Function to export metadata to Excel
    """
    print("Exporting metadata to Excel...")
    export_metadata_to_excel_fn(processed_path, excel_output_path)
    print("Excel export complete.")


def task_copy_matching(
    processed_path: Path,
    regex_pattern: str,
    copy_dest_folder: str,
    copy_matching_files_fn: Callable,
):
    """Copy files matching a regex pattern.

    Args:
        processed_path: Source directory
        regex_pattern: Pattern to match filenames
        copy_dest_folder: Destination folder
        copy_matching_files_fn: Function to copy matching files
    """
    if not regex_pattern or not copy_dest_folder:
        print("For 'copy_matching', --regex_pattern and --copy_dest_folder are required.")
        return
    stats = copy_matching_files_fn(processed_path, regex_pattern, Path(copy_dest_folder))
    print(f"Copied {stats['copied']} files matching '{regex_pattern}' to {copy_dest_folder}")


def task_export_all_dates(
    processed_path: Path,
    export_base_dir: Path,
    copy_matching_files_fn: Callable,
    directory_has_changed_fn: Callable,
    validate_merged_pdf_fn: Callable,
    run_merge: bool = False,
):
    """Export files for all unique dates found in processed files.

    Args:
        processed_path: Directory containing processed files
        export_base_dir: Base directory for exports
        copy_matching_files_fn: Function to copy matching files
        directory_has_changed_fn: Function to check if directory changed
        validate_merged_pdf_fn: Function to validate merged PDF
        run_merge: Whether to run PDF merge after export
    """
    processed_path = Path(processed_path)
    export_base_dir = Path(export_base_dir)

    print("Scanning for unique dates in processed files...")
    all_dates = get_unique_dates(processed_path)

    if not all_dates:
        print("No dates found in processed files.")
        return

    print(f"Found {len(all_dates)} unique dates: {', '.join(all_dates[:10])}{' ...' if len(all_dates) > 10 else ''}")

    total_copied = 0
    total_skipped = 0
    changed_directories = []

    for date in all_dates:
        export_date_dir = export_base_dir / date
        print(f"\n[{date}] Processing...")

        stats = copy_matching_files_fn(processed_path, date, export_date_dir, incremental=True)
        total_copied += stats['copied']
        total_skipped += stats['skipped']

        if stats['total'] == 0:
            print(f"  No files match date pattern '{date}'")
        else:
            print(f"  Copied: {stats['copied']}, Skipped: {stats['skipped']}, Total: {stats['total']}")

        if stats['copied'] > 0:
            changed_directories.append(export_date_dir)
        elif stats['total'] > 0:
            if export_date_dir.exists() and directory_has_changed_fn(export_date_dir):
                changed_directories.append(export_date_dir)

    print("\n=== Summary ===")
    print(f"Processed {len(all_dates)} date(s)")
    print(f"Total files copied: {total_copied}")
    print(f"Total files skipped (unchanged): {total_skipped}")
    print(f"Directories with changes: {len(changed_directories)}")

    if run_merge and changed_directories:
        print("\n=== Running PDF Merge ===")
        from shutil import which
        if which("pdf-merger") is None:
            print("WARNING: pdf-merger tool not found in PATH. Skipping merge step.")
        else:
            for export_dir in changed_directories:
                print(f"\nMerging PDFs in {export_dir}...")
                result = subprocess.run(f'pdf-merger "{export_dir}"', shell=True, text=True)
                if result.returncode == 0:
                    print("  Merge completed successfully")
                    validate_merged_pdf_fn(export_dir)
                else:
                    print(f"  Merge failed with exit code {result.returncode}")

    print("\nExport all dates complete.")


def task_check_files_exist(
    processed_path: Path,
    check_schema_path: str,
    check_files_exist_fn: Callable,
):
    """Check that expected files exist.

    Args:
        processed_path: Directory to check
        check_schema_path: Path to validation schema
        check_files_exist_fn: Function to check file existence
    """
    if not check_schema_path:
        print("For 'check_files_exist', --check_schema_path is required.")
        return
    check_files_exist_fn(processed_path, Path(check_schema_path))
    print("File existence check complete.")


def task_bootstrap_mappings(processed_path: Path, mappings_manager):
    """Populate mappings from existing metadata JSON files.

    Scans all metadata files and extracts raw -> canonical pairs,
    adding them as 'confirmed' mappings (since they're already in use).

    Args:
        processed_path: Directory containing metadata files
        mappings_manager: MappingsManager instance
    """
    if mappings_manager is None:
        print("Error: Mappings manager not initialized.")
        return

    json_files = list(processed_path.rglob("*.json"))
    if not json_files:
        print(f"No metadata files found in {processed_path}")
        return

    doc_type_count = 0
    issuer_count = 0
    skipped = 0

    for metadata_path in tqdm(json_files, desc="Scanning metadata"):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Get raw and normalized values
            doc_type_raw = data.get("document_type_raw")
            doc_type = data.get("document_type")
            issuing_party_raw = data.get("issuing_party_raw")
            issuing_party = data.get("issuing_party")

            # Add document type mapping if we have both raw and normalized
            if doc_type_raw and doc_type and doc_type != "$UNKNOWN$":
                existing = mappings_manager.get_mapping(doc_type_raw, "document_types")
                if existing is None:
                    mappings_manager.add_mapping(
                        doc_type_raw, doc_type, "document_types", confirmed=True, save=False
                    )
                    doc_type_count += 1

            # Add issuing party mapping if we have both raw and normalized
            if issuing_party_raw and issuing_party and issuing_party != "$UNKNOWN$":
                existing = mappings_manager.get_mapping(issuing_party_raw, "issuing_parties")
                if existing is None:
                    mappings_manager.add_mapping(
                        issuing_party_raw, issuing_party, "issuing_parties", confirmed=True, save=False
                    )
                    issuer_count += 1

        except Exception as e:
            skipped += 1
            if skipped <= 5:
                print(f"Skipping {metadata_path.name}: {e}")

    # Save all at once
    mappings_manager._save()

    print("\nBootstrap complete:")
    print(f"  Document type mappings added: {doc_type_count}")
    print(f"  Issuing party mappings added: {issuer_count}")
    print(f"  Files skipped: {skipped}")
    print(f"\nMappings saved to: {mappings_manager.path}")

    # Show stats
    stats = mappings_manager.get_stats()
    print("\nCurrent mappings stats:")
    for field, counts in stats.items():
        print(f"  {field}: {counts['confirmed']} confirmed, {counts['auto']} auto, {counts['canonicals']} canonicals")


def task_review_mappings(mappings_manager):
    """Interactive review of auto-added mappings.

    Args:
        mappings_manager: MappingsManager instance
    """
    if mappings_manager is None:
        print("Error: Mappings manager not initialized.")
        return

    # Get auto mappings for both fields
    doc_auto = mappings_manager.get_auto_mappings("document_types")
    issuer_auto = mappings_manager.get_auto_mappings("issuing_parties")

    total_pending = len(doc_auto) + len(issuer_auto)

    if total_pending == 0:
        print("No auto-added mappings pending review.")
        stats = mappings_manager.get_stats()
        print("\nCurrent mappings stats:")
        for field, counts in stats.items():
            print(f"  {field}: {counts['confirmed']} confirmed, {counts['auto']} auto")
        return

    print("=" * 60)
    print("AUTO-ADDED MAPPINGS AWAITING REVIEW")
    print("=" * 60)
    print()

    if doc_auto:
        print(f"Document Types ({len(doc_auto)} pending):")
        for i, (raw, canonical) in enumerate(doc_auto.items(), 1):
            print(f"  {i}. \"{raw}\" -> \"{canonical}\"")
        print()

    if issuer_auto:
        print(f"Issuing Parties ({len(issuer_auto)} pending):")
        for i, (raw, canonical) in enumerate(issuer_auto.items(), 1):
            print(f"  {i}. \"{raw}\" -> \"{canonical}\"")
        print()

    print("Options:")
    print("  [a] Confirm ALL mappings")
    print("  [r] Review one-by-one")
    print("  [q] Quit without changes")
    print()

    choice = input("Select option: ").strip().lower()

    if choice == 'a':
        # Confirm all
        doc_confirmed = mappings_manager.confirm_all("document_types", save=False)
        issuer_confirmed = mappings_manager.confirm_all("issuing_parties", save=True)
        print(f"\nConfirmed {doc_confirmed} document type mappings and {issuer_confirmed} issuer mappings.")

    elif choice == 'r':
        # Review one-by-one
        _review_field_mappings(mappings_manager, "document_types", doc_auto)
        _review_field_mappings(mappings_manager, "issuing_parties", issuer_auto)
        print("\nReview complete.")

    else:
        print("No changes made.")


def _review_field_mappings(mappings_manager, field: str, mappings_dict: dict):
    """Helper to review mappings for a single field."""
    if not mappings_dict:
        return

    field_label = "Document Type" if field == "document_types" else "Issuing Party"
    print(f"\n--- Reviewing {field_label} Mappings ---")

    for raw, canonical in list(mappings_dict.items()):
        print(f"\n\"{raw}\" -> \"{canonical}\"")
        print("  [c] Confirm  [e] Edit canonical  [r] Reject  [s] Skip")
        action = input("  Action: ").strip().lower()

        if action == 'c':
            mappings_manager.confirm_mapping(raw, field, save=True)
            print("  Confirmed.")
        elif action == 'e':
            new_canonical = input("  Enter new canonical value: ").strip()
            if new_canonical:
                mappings_manager.update_mapping(raw, new_canonical, field, confirm=True, save=True)
                print(f"  Updated to \"{new_canonical}\" and confirmed.")
            else:
                print("  No change (empty input).")
        elif action == 'r':
            mappings_manager.reject_mapping(raw, field, save=True)
            print("  Rejected (removed).")
        else:
            print("  Skipped.")


def task_add_canonical(mappings_manager, field: str, canonical: str):
    """Add a new canonical value to the mappings.

    Args:
        mappings_manager: MappingsManager instance
        field: Field name (document_type or issuing_party)
        canonical: Canonical value to add
    """
    if mappings_manager is None:
        print("Error: Mappings manager not initialized.")
        return

    # Normalize field name
    field_map = {
        "document_type": "document_types",
        "document_types": "document_types",
        "issuing_party": "issuing_parties",
        "issuing_parties": "issuing_parties",
    }

    normalized_field = field_map.get(field.lower())
    if not normalized_field:
        print(f"Error: Unknown field '{field}'. Use 'document_type' or 'issuing_party'.")
        return

    if mappings_manager.add_canonical(normalized_field, canonical):
        print(f"Added canonical '{canonical}' to {normalized_field}.")
        print(f"Current canonicals: {', '.join(mappings_manager.get_canonicals(normalized_field))}")
    else:
        print(f"Canonical '{canonical}' already exists in {normalized_field}.")


def task_gmail_download():
    """Download email attachments from Gmail."""
    from documentor.gmail import download_gmail_attachments

    profile = get_current_profile()
    if not profile:
        print("Error: No profile is active.")
        sys.exit(1)

    raw_paths = profile.paths.raw
    processed_path = profile.paths.processed

    if not raw_paths or not processed_path:
        missing = []
        if not raw_paths:
            missing.append("paths.raw")
        if not processed_path:
            missing.append("paths.processed")
        print(f"Missing required profile settings: {', '.join(missing)}")
        sys.exit(1)

    # Use first raw path for downloads
    raw_path = Path(raw_paths[0])
    processed_path = Path(processed_path)

    raw_path.mkdir(parents=True, exist_ok=True)

    end_date = datetime.now()

    # Get most recent date from processed files
    unique_dates = get_unique_dates(processed_path) if processed_path.exists() else []

    if unique_dates:
        most_recent = unique_dates[0]  # Already sorted descending
        # Parse YYYY-MM to first day of that month
        start_date = datetime.strptime(f"{most_recent}-01", "%Y-%m-%d")
        print(f"Date range: {start_date.date()} to {end_date.date()}")
    else:
        # Default to last 30 days if no processed files
        start_date = end_date - timedelta(days=30)
        print("No processed files found. Using default range: last 30 days")

    print(f"Downloading attachments to: {raw_path}")

    stats = download_gmail_attachments(
        output_dir=raw_path,
        start_date=start_date,
        end_date=end_date,
    )

    print("\n=== Gmail Download Summary ===")
    print(f"Messages found: {stats['messages_found']}")
    print(f"Messages processed: {stats['messages_processed']}")
    print(f"Messages skipped: {stats['messages_skipped']}")
    print(f"Attachments downloaded: {stats['attachments_downloaded']}")
    print(f"Attachments failed: {stats['attachments_failed']}")
    print(f"Bytes downloaded: {stats['bytes_downloaded']:,}")
