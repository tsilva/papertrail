"""
Documentor - AI-powered PDF document classification and organization.

Main CLI entry point for processing PDF documents.
"""

import os
import re
import json
import shutil
import hashlib
import unicodedata
import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd
from tqdm import tqdm

# Import from documentor package
from documentor.config import (
    load_env,
    get_config_paths,
    get_openai_client,
    set_current_profile,
)
from documentor.profiles import (
    load_profile,
    list_available_profiles,
    get_profiles_dir,
    ProfileNotFoundError,
    ProfileError,
)
from documentor.hashing import hash_file_fast, hash_file_content
from documentor.logging_utils import setup_failure_logger, log_failure
from documentor.models import (
    DocumentMetadata,
    DocumentMetadataRaw,
    normalize_enum_field_in_dict,
)
from documentor.enums import reset_enum_cache
from documentor.llm import (
    get_system_prompt_raw_extraction,
    TOOLS_RAW_EXTRACTION,
    normalize_metadata,
)
from documentor.mappings import MappingsManager
from documentor.pdf import render_pdf_to_images, find_pdf_files, get_page_count
from documentor.metadata import (
    build_hash_index,
    get_unique_dates,
    save_metadata_json,
)
from documentor.tasks import (
    task_extract_new,
    task_rename_files,
    task_validate_metadata,
    task_export_excel,
    task_copy_matching,
    task_export_all_dates,
    task_check_files_exist,
    task_bootstrap_mappings,
    task_review_mappings,
    task_add_canonical,
    task_gmail_download,
)

# ------------------- CONFIG -------------------

# Module-level globals (initialized by initialize_config)
CONFIG_PATHS = None
OPENROUTER_MODEL_ID = None
openai_client = None

# Global failure logger
failure_logger = None

# Global mappings manager
mappings_manager = None


def initialize_config(profile_name: Optional[str] = None) -> None:
    """
    Initialize configuration from profile.

    Args:
        profile_name: Profile name to load, or None for auto-detection (uses 'default')

    Raises:
        ProfileNotFoundError: If specified profile doesn't exist
        ProfileError: If profile loading fails
    """
    global CONFIG_PATHS, OPENROUTER_MODEL_ID, openai_client, mappings_manager

    # Load .env for environment variable expansion in profiles (e.g., ${OPENROUTER_API_KEY})
    load_env()

    # Check if profiles directory exists
    profiles_dir = get_profiles_dir()
    profiles_exist = profiles_dir.exists() and any(profiles_dir.glob("*.yaml"))

    if not profiles_exist:
        raise ProfileNotFoundError(
            "No profiles found. Create a profile from profiles/*.yaml.example. "
            "See profiles/README.md for documentation."
        )

    # Load profile (explicit or auto-detect 'default')
    if profile_name is not None:
        profile = load_profile(profile_name)
        print(f"Using profile: {profile.profile.name}")
    else:
        available = list_available_profiles()
        if "default" in available:
            profile = load_profile("default")
            print(f"Using profile: {profile.profile.name} (auto-detected)")
        else:
            raise ProfileNotFoundError(
                f"No 'default' profile found. Available profiles: {', '.join(available)}. "
                "Use --profile to specify one, or create profiles/default.yaml."
            )

    if profile.profile.description:
        print(f"  {profile.profile.description}")

    set_current_profile(profile)

    # Reset enum cache so it loads from profile settings
    reset_enum_cache()

    # Load configuration paths from profile
    CONFIG_PATHS = get_config_paths()

    # Get model ID from profile
    OPENROUTER_MODEL_ID = profile.openrouter.model_id

    # Initialize OpenAI client from profile
    openai_client = get_openai_client()

    # Initialize mappings manager
    config_dir = Path(__file__).parent / "config"
    mappings_path = config_dir / "mappings.yaml"
    mappings_manager = MappingsManager(mappings_path)

# ------------------- UTILS -------------------

def sanitize_filename_component(s: str) -> str:
    """Sanitize a string for use in a filename."""
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[\\/*?:"<>|()\[\],]', '', s).strip()
    return re.sub(r'\s+', ' ', s)


def file_name_from_metadata(metadata: DocumentMetadata, file_hash: str) -> str:
    """Generate a filename from metadata."""
    parts = [
        sanitize_filename_component(metadata.issue_date),
        sanitize_filename_component(metadata.document_type.value),
        sanitize_filename_component(metadata.issuing_party.value)
    ]

    if metadata.service_name:
        parts.append(sanitize_filename_component(metadata.service_name))

    if metadata.total_amount is not None:
        amount = f"{metadata.total_amount:.0f}" if metadata.total_amount.is_integer() else f"{metadata.total_amount:.2f}"
        currency = metadata.total_amount_currency or ""
        parts.append(sanitize_filename_component(f"{amount} {currency}".strip()))

    parts.append(f"{file_hash}.pdf")
    return " - ".join(parts).lower()


# ------------------- CLASSIFICATION -------------------

def classify_pdf_document(pdf_path: Path, file_hash: str) -> DocumentMetadata:
    """Classify a PDF document using the LLM."""
    global failure_logger
    client = openai_client

    try:
        images_b64 = render_pdf_to_images(pdf_path)
    except Exception as e:
        log_failure(failure_logger, pdf_path, e)
        raise RuntimeError(f"Failed to render PDF image: {pdf_path}") from e

    try:
        # PHASE 1: Raw Extraction
        user_content = [
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}
            }
            for img_b64 in images_b64
        ]

        messages = [
            {"role": "system", "content": get_system_prompt_raw_extraction()},
            {"role": "user", "content": user_content},
        ]

        response = client.chat.completions.create(
            model=OPENROUTER_MODEL_ID,
            max_tokens=4096,
            temperature=0,
            messages=messages,
            tools=TOOLS_RAW_EXTRACTION,
            tool_choice={"type": "function", "function": {"name": "extract_document_metadata"}},
        )

        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls:
            raise ValueError("OpenRouter did not return structured classification.")

        args = tool_calls[0].function.arguments
        raw_metadata = DocumentMetadataRaw.model_validate_json(args)

        # PHASE 2: Normalization (with two-tier mapping lookup)
        normalized_doc_type, normalized_issuing_party = normalize_metadata(
            raw_metadata, client, OPENROUTER_MODEL_ID, mappings=mappings_manager
        )

        # Create final metadata with both raw and normalized values
        metadata = DocumentMetadata(
            issue_date=raw_metadata.issue_date,
            document_type=normalized_doc_type,
            issuing_party=normalized_issuing_party,
            service_name=raw_metadata.service_name,
            total_amount=raw_metadata.total_amount,
            total_amount_currency=raw_metadata.total_amount_currency,
            confidence=raw_metadata.confidence,
            reasoning=raw_metadata.reasoning,
            content_hash=file_hash,
            document_type_raw=raw_metadata.document_type,
            issuing_party_raw=raw_metadata.issuing_party,
        )

        now = datetime.now().strftime("%Y-%m-%d")
        metadata.create_date = now
        metadata.update_date = now
        return metadata
    except Exception as e:
        log_failure(failure_logger, pdf_path, e)
        raise RuntimeError(f"Classification failed for: {pdf_path}") from e


# ------------------- RENAMING & PROCESSING -------------------

def rename_single_pdf(pdf_path: Path, content_hash: str, processed_path: Path, known_hashes: set):
    """Process and rename a single PDF file."""
    global failure_logger
    try:
        file_hash = hash_file_fast(pdf_path)
        metadata = classify_pdf_document(pdf_path, content_hash)
        metadata.file_hash = file_hash

        filename = file_name_from_metadata(metadata, content_hash)
        new_pdf_path = processed_path / filename

        shutil.copy2(pdf_path, new_pdf_path)
        save_metadata_json(new_pdf_path, metadata)

        known_hashes.add(content_hash)
        known_hashes.add(file_hash)
        print(f"Processed: {pdf_path.name} -> {filename}")
    except Exception as e:
        log_failure(failure_logger, pdf_path, e)
        print(f"Failed to process {pdf_path.name}: {e}")


def rename_pdf_files(pdf_paths, file_hash_map, known_hashes, processed_path):
    """Rename multiple PDF files."""
    for pdf_path in tqdm(pdf_paths):
        rename_single_pdf(pdf_path, file_hash_map[pdf_path], processed_path, known_hashes)


def validate_metadata(output_path: Path):
    """Validate metadata files and their corresponding PDFs."""
    valid_entries = []
    errors = []
    json_files = list(output_path.rglob("*.json"))

    for metadata_path in tqdm(json_files, desc="Validating metadata"):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = DocumentMetadata.model_validate(data)

            content_hash = metadata.content_hash
            if not content_hash:
                raise ValueError("Missing 'content_hash' in metadata.")

            pdf_path = metadata_path.with_suffix(".pdf")
            if not pdf_path.exists():
                raise FileNotFoundError(f"Missing PDF for metadata: {pdf_path.name}")

            actual_hash = hash_file_content(pdf_path)
            if content_hash != actual_hash:
                raise ValueError(f"Hash mismatch: metadata content_hash is '{content_hash}', actual is '{actual_hash}'.")

            if content_hash not in pdf_path.name:
                raise ValueError(f"Filename '{pdf_path.name}' does not include the expected hash '{content_hash}'.")

            valid_entries.append((pdf_path, metadata))

        except Exception as e:
            errors.append((metadata_path, str(e)))

    if errors:
        print("\nValidation errors found:")
        for meta_path, err in errors:
            print(f"- {meta_path}: {err}")
    else:
        print("\nAll metadata files passed validation.")

    return valid_entries


def validate_merged_pdf(folder_path: Path) -> bool:
    """
    Validate that merged_all.pdf has the correct page count.

    Compares the page count of merged_all.pdf against the sum of
    page counts of all other PDFs in the folder.

    Returns True if valid, raises AssertionError if mismatch.
    """
    merged_path = folder_path / "merged_all.pdf"
    if not merged_path.exists():
        print(f"  No merged_all.pdf found in {folder_path}")
        return True

    source_pdfs = [p for p in folder_path.glob("*.pdf") if p.name != "merged_all.pdf"]
    expected_pages = sum(get_page_count(pdf) for pdf in source_pdfs)

    actual_pages = get_page_count(merged_path)

    if actual_pages != expected_pages:
        raise AssertionError(
            f"Merged PDF page count mismatch in {folder_path}: "
            f"expected {expected_pages} pages (from {len(source_pdfs)} files), "
            f"got {actual_pages} pages"
        )

    print(f"  Merge validation passed: {actual_pages} pages from {len(source_pdfs)} files")
    return True


def export_metadata_to_excel(processed_path: Path, excel_output_path: str):
    """Export metadata to an Excel file."""
    from enum import Enum
    from documentor.metadata import iter_metadata_files

    metadata_list = []

    for metadata_path, metadata in iter_metadata_files(processed_path, show_progress=True, progress_desc="Collecting metadata"):
        metadata_dict = metadata.model_dump()

        metadata_dict.pop("reasoning", None)

        pdf_path = metadata_path.with_suffix(".pdf")
        filename = pdf_path.name if pdf_path.exists() else ""
        metadata_dict["filename"] = filename
        metadata_dict["filename_length"] = len(filename)

        try:
            date_parts = metadata.issue_date.split('-')
            metadata_dict["year"] = int(date_parts[0])
            metadata_dict["month"] = int(date_parts[1])
        except (IndexError, ValueError, AttributeError):
            metadata_dict["year"] = None
            metadata_dict["month"] = None

        # Normalize enum fields
        normalize_enum_field_in_dict(metadata_dict, "document_type", "DocumentType")
        normalize_enum_field_in_dict(metadata_dict, "issuing_party", "IssuingParty")

        metadata_list.append(metadata_dict)

    if metadata_list:
        df = pd.DataFrame(metadata_list)
        ordered_cols = [
            "confidence", "issue_date", "year", "month", "content_hash", "file_hash",
            "filename", "filename_length", "document_type", "document_type_raw",
            "issuing_party", "issuing_party_raw", "service_name",
            "total_amount", "total_amount_currency"
        ]
        extra_cols = [col for col in df.columns if col not in ordered_cols]
        df = df[ordered_cols + extra_cols]

        if "issue_date" in df.columns:
            df = df.sort_values(by="issue_date", ascending=False)

        with pd.ExcelWriter(excel_output_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Sheet1')
            worksheet = writer.sheets['Sheet1']
            worksheet.freeze_panes = 'A2'

            from openpyxl.utils import get_column_letter
            for col in ordered_cols:
                if col in df.columns:
                    col_idx = df.columns.get_loc(col) + 1
                    col_letter = get_column_letter(col_idx)
                    values_lens = [len(str(val)) for val in df[col].values if val is not None]
                    max_len = max(values_lens + [len(col)])
                    worksheet.column_dimensions[col_letter].width = min(max_len + 2, 102)

            hidden_cols = ["year", "month", "filename_length"]
            for col in hidden_cols:
                if col in df.columns:
                    col_letter = get_column_letter(df.columns.get_loc(col) + 1)
                    worksheet.column_dimensions[col_letter].hidden = True

        print(f"\nExported {len(df)} entries to {excel_output_path}")
    else:
        print("\nNo valid metadata found to export.")


def copy_matching_files(
    processed_path: Path,
    regex_pattern: str,
    dest_folder: Path,
    incremental: bool = False
) -> dict:
    """
    Copy files matching regex pattern to destination.

    Args:
        processed_path: Source directory
        regex_pattern: Pattern to match filenames
        dest_folder: Destination directory
        incremental: If True, skip files that already exist with same content

    Returns:
        Stats dict with 'copied', 'skipped', 'total' counts
    """
    dest_folder.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(regex_pattern)
    stats = {'copied': 0, 'skipped': 0, 'total': 0}

    for file in processed_path.iterdir():
        if not file.is_file():
            continue
        if file.suffix.lower() not in [".pdf", ".json"]:
            continue
        if not pattern.search(file.name):
            continue

        stats['total'] += 1
        dest_file = dest_folder / file.name

        should_copy = True
        if incremental and dest_file.exists():
            if file.stat().st_size == dest_file.stat().st_size:
                src_hash = hash_file_fast(file)
                dst_hash = hash_file_fast(dest_file)
                if src_hash == dst_hash:
                    should_copy = False
                    stats['skipped'] += 1

        if should_copy:
            shutil.copy2(file, dest_file)
            stats['copied'] += 1

    return stats


def calculate_directory_hash(directory: Path) -> str:
    """Calculate a hash representing all PDF files in the directory."""
    pdf_files = sorted(directory.glob("*.pdf"))
    if not pdf_files:
        return ""

    combined = []
    for pdf_file in pdf_files:
        file_hash = hash_file_fast(pdf_file)
        combined.append(f"{pdf_file.name}:{file_hash}")

    combined_str = "\n".join(combined)
    return hashlib.sha256(combined_str.encode()).hexdigest()[:16]


def directory_has_changed(directory: Path) -> bool:
    """Check if directory contents have changed since last check."""
    hash_file_path = directory / ".directory_hash"
    current_hash = calculate_directory_hash(directory)

    if not current_hash:
        return False

    if not hash_file_path.exists():
        with open(hash_file_path, "w") as f:
            f.write(current_hash)
        return True

    with open(hash_file_path, "r") as f:
        stored_hash = f.read().strip()

    if current_hash != stored_hash:
        with open(hash_file_path, "w") as f:
            f.write(current_hash)
        return True

    return False


def check_files_exist(target_folder: Path, validation_schema_path: Path):
    """Validate files exist based on a schema."""
    with open(validation_schema_path, "r", encoding="utf-8") as f:
        checks = json.load(f)

    json_files = list(target_folder.glob("*.json"))
    file_data = []
    for json_path in json_files:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            file_data.append((json_path, data))
        except Exception as e:
            print(f"Skipping {json_path.name}: {e}")

    check_results = []
    for idx, check in enumerate(checks):
        found = any(
            all(str(data.get(k, "")).strip() == str(v).strip() for k, v in check.items())
            for _, data in file_data
        )
        check_results.append((found, idx, check))

    all_passed = all(found for found, _, _ in check_results)

    # Sort: found items first, then by index
    sorted_results = sorted(check_results, key=lambda x: (not x[0], x[1]))
    for found, idx, check in sorted_results:
        status = "[OK]" if found else "[FAIL]"
        result = "FOUND" if found else "NOT FOUND"
        print(f"{status} {check} -- {result}")

    if all_passed:
        print("\nAll file existence checks passed.")
    else:
        print("\nSome file existence checks failed.")


# ------------------- PIPELINE -------------------

def run_step(cmd, step_desc):
    """Run a pipeline step."""
    print(f"### {step_desc}...")
    result = subprocess.run(cmd, shell=True, text=True)
    if result.returncode != 0:
        print(f"{step_desc} failed with exit code {result.returncode}.")
        sys.exit(result.returncode)
    print(f"### {step_desc}... Finished.")


def pipeline(export_date_arg=None):
    """Run the full document processing pipeline."""
    from shutil import which
    from datetime import timedelta

    RAW_FILES_DIR = os.getenv("RAW_FILES_DIR")
    PROCESSED_FILES_DIR = os.getenv("PROCESSED_FILES_DIR")
    EXPORT_FILES_DIR = os.getenv("EXPORT_FILES_DIR")

    required_vars = [
        ("RAW_FILES_DIR", RAW_FILES_DIR),
        ("PROCESSED_FILES_DIR", PROCESSED_FILES_DIR),
        ("EXPORT_FILES_DIR", EXPORT_FILES_DIR),
    ]
    missing = [name for name, val in required_vars if not val]
    if missing:
        print(f"Missing required .env variables: {', '.join(missing)}")
        sys.exit(1)

    for tool in ["mbox-extractor", "archive-extractor", "pdf-merger"]:
        if which(tool) is None:
            print(f"Required tool '{tool}' not found in PATH. Please install it and try again.")
            sys.exit(1)

    if export_date_arg:
        export_date = export_date_arg
    else:
        today = datetime.now()
        first_of_this_month = today.replace(day=1)
        last_month = first_of_this_month - timedelta(days=1)
        export_date = last_month.strftime("%Y-%m")

    if not re.match(r"^\d{4}-\d{2}$", export_date):
        print("The export_date must be in YYYY-MM format.")
        sys.exit(1)

    export_date_dir = os.path.join(EXPORT_FILES_DIR, export_date)

    # Get passwords from profile (inline or file reference)
    from documentor.config import get_passwords, get_validations
    passwords, passwords_file = get_passwords()

    # Track temp files for cleanup
    temp_passwords_file = None
    temp_validations_file = None

    # Setup passwords file path for archive-extractor tool
    if passwords:
        if passwords_file:
            # Use profile file reference directly
            zip_passwords_file_path = passwords_file
            print(f"Using passwords file from profile: {zip_passwords_file_path}")
        else:
            # Create temp file for inline data
            temp_passwords = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt')
            temp_passwords.write('\n'.join(passwords))
            temp_passwords.close()
            zip_passwords_file_path = temp_passwords.name
            temp_passwords_file = temp_passwords.name
            print(f"Created temporary passwords file: {zip_passwords_file_path}")
    else:
        print("Warning: No passwords configured. Skipping password-protected archives.")
        zip_passwords_file_path = None

    # Get validations from profile (inline or file reference)
    validations, validations_file = get_validations()

    # Setup validations file path for check_files_exist task
    if validations and validations.get('rules'):
        if validations_file:
            # Use profile file reference directly
            validations_file_path = validations_file
            print(f"Using validations file from profile: {validations_file_path}")
        else:
            # Create temp file for inline data
            temp_validations = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
            json.dump(validations['rules'], temp_validations, indent=2)
            temp_validations.close()
            validations_file_path = temp_validations.name
            temp_validations_file = temp_validations.name
            print(f"Created temporary validations file: {validations_file_path}")
    else:
        validations_file_path = None

    processed_files_excel_path = Path(PROCESSED_FILES_DIR) / "processed_files.xlsx"
    raw_dirs = [p for p in RAW_FILES_DIR.split(';') if p]

    run_step(f'"{sys.executable}" "{__file__}" gmail_download', "Step 1: Download Gmail attachments")

    for rd in raw_dirs:
        run_step(f'mbox-extractor "{rd}"', "Step 2: Google Takeout mbox extraction")
        if zip_passwords_file_path:
            run_step(f'archive-extractor "{rd}" --passwords "{zip_passwords_file_path}"', "Step 3: Google Takeout zip extraction")
        else:
            run_step(f'archive-extractor "{rd}"', "Step 3: Google Takeout zip extraction")

    raw_dirs_arg = ";".join(raw_dirs)
    run_step(f'"{sys.executable}" "{__file__}" extract_new "{PROCESSED_FILES_DIR}" --raw_path "{raw_dirs_arg}"', "Step 4: Extract new documents")
    run_step(f'"{sys.executable}" "{__file__}" rename_files "{PROCESSED_FILES_DIR}"', "Step 5: Rename files and metadata")
    run_step(f'"{sys.executable}" "{__file__}" export_excel "{PROCESSED_FILES_DIR}" --excel_output_path "{processed_files_excel_path}"', "Step 6: Export metadata to Excel")
    run_step(f'"{sys.executable}" "{__file__}" copy_matching "{PROCESSED_FILES_DIR}" --regex_pattern "{export_date}" --copy_dest_folder "{export_date_dir}"', "Step 7: Copy matching documents")
    run_step(f'pdf-merger "{export_date_dir}"', "Step 8: Merge PDFs")
    validate_merged_pdf(Path(export_date_dir))
    if validations_file_path:
        run_step(f'"{sys.executable}" "{__file__}" check_files_exist "{export_date_dir}" --check_schema_path "{validations_file_path}"', "Step 9: Validate exported files")
    else:
        print("Step 9: Skipping file validation (no validation rules configured in profile)")

    # Cleanup temporary files
    if temp_passwords_file:
        try:
            os.unlink(temp_passwords_file)
            print(f"Cleaned up temporary passwords file: {temp_passwords_file}")
        except Exception as e:
            print(f"Warning: Failed to cleanup temporary passwords file: {e}")

    if temp_validations_file:
        try:
            os.unlink(temp_validations_file)
            print(f"Cleaned up temporary validations file: {temp_validations_file}")
        except Exception as e:
            print(f"Warning: Failed to cleanup temporary validations file: {e}")

    print("All steps completed successfully.")


def process_folder(task: str, processed_path: str, raw_paths=None, excel_output_path: str = None,
                   regex_pattern: str = None, copy_dest_folder: str = None, check_schema_path: str = None,
                   export_base_dir: str = None, run_merge: bool = False):
    """Dispatch to appropriate task handler."""
    if raw_paths is not None:
        raw_paths = [Path(p) for p in raw_paths]
    processed_path = Path(processed_path)
    processed_path.mkdir(parents=True, exist_ok=True)

    # Define task handlers using imported task functions
    task_handlers = {
        "extract_new": lambda: task_extract_new(
            processed_path, raw_paths, rename_pdf_files
        ),
        "rename_files": lambda: task_rename_files(
            processed_path, file_name_from_metadata
        ),
        "validate_metadata": lambda: task_validate_metadata(
            processed_path, validate_metadata
        ),
        "export_excel": lambda: task_export_excel(
            processed_path, excel_output_path, export_metadata_to_excel
        ),
        "copy_matching": lambda: task_copy_matching(
            processed_path, regex_pattern, copy_dest_folder, copy_matching_files
        ),
        "export_all_dates": lambda: task_export_all_dates(
            processed_path, export_base_dir,
            copy_matching_files, directory_has_changed, validate_merged_pdf,
            run_merge
        ),
        "check_files_exist": lambda: task_check_files_exist(
            processed_path, check_schema_path, check_files_exist
        ),
    }

    handler = task_handlers.get(task)
    if handler:
        handler()
    else:
        print("Invalid task specified.")


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
    parser.add_argument("task", type=str, choices=[
        'extract_new', 'rename_files', 'validate_metadata', 'export_excel',
        'copy_matching', 'export_all_dates', 'check_files_exist', 'pipeline',
        'gmail_download', 'bootstrap_mappings', 'review_mappings', 'add_canonical'
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
    args = parser.parse_args()

    # Initialize configuration (profile or legacy .env)
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
        task_review_mappings(mappings_manager)
        return

    if args.task == "add_canonical":
        if not args.field or not args.canonical:
            parser.error("add_canonical requires --field and --canonical arguments.")
        task_add_canonical(mappings_manager, args.field, args.canonical)
        return

    if args.task == "bootstrap_mappings":
        if not args.processed_path:
            parser.error("bootstrap_mappings requires the processed_path argument.")
        if not os.path.exists(args.processed_path):
            parser.error(f"The processed_path '{args.processed_path}' does not exist.")
        task_bootstrap_mappings(Path(args.processed_path), mappings_manager)
        return

    if not args.processed_path:
        parser.error("the processed_path argument is required.")
    if not os.path.exists(args.processed_path):
        parser.error(f"The processed_path '{args.processed_path}' does not exist.")
    if not os.path.isdir(args.processed_path):
        parser.error(f"The processed_path '{args.processed_path}' is not a directory.")

    raw_paths = None
    if args.task == "extract_new":
        if not args.raw_path:
            parser.error("the --raw_path argument is required when task is 'extract_new'.")
        raw_paths = [p for p in args.raw_path.split(';') if p]
        if not raw_paths:
            parser.error("the --raw_path argument must contain at least one path.")
        for rp in raw_paths:
            if not os.path.exists(rp):
                parser.error(f"The raw_path '{rp}' does not exist.")
            if not os.path.isdir(rp):
                parser.error(f"The raw_path '{rp}' is not a directory.")

    if args.task == "export_excel":
        if not args.excel_output_path:
            parser.error("the --excel_output_path argument is required when task is 'export_excel'.")
        if not args.excel_output_path.endswith(".xlsx"):
            parser.error("the --excel_output_path argument must end with '.xlsx'.")

    if args.task == "copy_matching":
        if not args.regex_pattern:
            parser.error("the --regex_pattern argument is required when task is 'copy_matching'.")
        if not args.copy_dest_folder:
            parser.error("the --copy_dest_folder argument is required when task is 'copy_matching'.")
        if not os.path.exists(args.copy_dest_folder):
            os.makedirs(args.copy_dest_folder, exist_ok=True)
        if not os.path.isdir(args.copy_dest_folder):
            parser.error(f"The copy_dest_folder '{args.copy_dest_folder}' is not a directory.")

    export_base_dir = args.export_base_dir
    if args.task == "export_all_dates":
        if not export_base_dir:
            export_base_dir = os.getenv("EXPORT_FILES_DIR")
            if not export_base_dir:
                parser.error("the --export_base_dir argument is required when task is 'export_all_dates'.")
        if not os.path.exists(export_base_dir):
            os.makedirs(export_base_dir, exist_ok=True)
        if not os.path.isdir(export_base_dir):
            parser.error(f"The export_base_dir '{export_base_dir}' is not a directory.")

    check_schema_path = args.check_schema_path
    temp_check_schema_file = None
    if args.task == "check_files_exist":
        if not check_schema_path:
            # Get validations from profile
            from documentor.config import get_validations
            validations, validations_file = get_validations()
            if validations and validations.get('rules'):
                if validations_file:
                    check_schema_path = validations_file
                else:
                    # Create temp file for inline data
                    temp_check_schema = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.json')
                    json.dump(validations['rules'], temp_check_schema, indent=2)
                    temp_check_schema.close()
                    check_schema_path = temp_check_schema.name
                    temp_check_schema_file = temp_check_schema.name
            else:
                parser.error("No validation rules found in profile. Use --check_schema_path or configure validations in profile.")
        if not os.path.exists(check_schema_path):
            parser.error(f"The check_schema_path '{check_schema_path}' does not exist.")

    process_folder(
        args.task,
        args.processed_path,
        raw_paths=raw_paths if args.task == "extract_new" else None,
        excel_output_path=args.excel_output_path,
        regex_pattern=args.regex_pattern,
        copy_dest_folder=args.copy_dest_folder,
        export_base_dir=export_base_dir,
        run_merge=args.run_merge if hasattr(args, 'run_merge') else False,
        check_schema_path=check_schema_path if args.task == "check_files_exist" else args.check_schema_path
    )

    # Cleanup temp schema file if created
    if temp_check_schema_file:
        try:
            os.unlink(temp_check_schema_file)
        except Exception:
            pass


if __name__ == "__main__":
    main()
