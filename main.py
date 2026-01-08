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
from pathlib import Path
from datetime import datetime
from typing import Optional

import pandas as pd
from tqdm import tqdm

# Import from documentor package
from documentor.config import (
    ensure_home_config_and_env,
    get_openai_client,
)
from documentor.hashing import hash_file_fast, hash_file_content
from documentor.logging_utils import setup_failure_logger, log_failure
from documentor.models import (
    DocumentMetadata,
    DocumentMetadataRaw,
    DocumentType,
    IssuingParty,
    DOCUMENT_TYPES,
    ISSUING_PARTIES,
    normalize_enum_field_in_dict,
)
from documentor.llm import (
    get_system_prompt_raw_extraction,
    TOOLS_RAW_EXTRACTION,
    normalize_metadata,
)
from documentor.pdf import render_pdf_to_images, find_pdf_files
from documentor.metadata import (
    build_hash_index,
    get_unique_dates,
    save_metadata_json,
)

# ------------------- CONFIG -------------------

# Initialize config at module load
CONFIG_DIR, ENV_PATH = ensure_home_config_and_env()

from dotenv import load_dotenv
load_dotenv(dotenv_path=ENV_PATH, override=True)

OPENROUTER_MODEL_ID = os.getenv("OPENROUTER_MODEL_ID")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

openai_client = get_openai_client(OPENROUTER_API_KEY, OPENROUTER_BASE_URL)

# Global failure logger
failure_logger = None

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

        # PHASE 2: Normalization
        normalized_doc_type, normalized_issuing_party = normalize_metadata(
            raw_metadata, client, OPENROUTER_MODEL_ID
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


def rename_existing_files(output_path: Path):
    """Rename existing files based on their metadata."""
    valid_entries = validate_metadata(output_path)

    for old_pdf_path, metadata in valid_entries:
        content_hash = metadata.content_hash
        new_filename = file_name_from_metadata(metadata, content_hash)
        new_pdf_path = output_path / new_filename
        new_metadata_path = new_pdf_path.with_suffix(".json")

        if old_pdf_path == new_pdf_path:
            continue

        try:
            old_metadata_path = old_pdf_path.with_suffix(".json")
            shutil.move(old_pdf_path, new_pdf_path)
            shutil.move(old_metadata_path, new_metadata_path)
            print(f"Renamed: {old_pdf_path.name} -> {new_filename}")
        except Exception as e:
            print(f"Failed to rename {old_pdf_path.name}: {e}")


def export_metadata_to_excel(processed_path: Path, excel_output_path: str):
    """Export metadata to an Excel file."""
    from enum import Enum

    metadata_list = []
    json_files = list(processed_path.rglob("*.json"))

    for metadata_path in tqdm(json_files, desc="Collecting metadata"):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = DocumentMetadata.model_validate(data)
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
        except Exception as e:
            print(f"Skipping {metadata_path.name}: {e}")

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

            for col in ordered_cols:
                if col in df.columns:
                    col_idx = df.columns.get_loc(col) + 1
                    max_len = max(
                        [len(str(val)) if val is not None else 0 for val in df[col].values] + [len(col)]
                    )
                    max_len = min(max_len, 100)
                    worksheet.column_dimensions[chr(64 + col_idx)].width = max_len + 2

            for col in ["year", "month", "filename_length"]:
                if col in df.columns:
                    col_idx = df.columns.get_loc(col) + 1
                    worksheet.column_dimensions[chr(64 + col_idx)].hidden = True

        print(f"\nExported {len(df)} entries to {excel_output_path}")
    else:
        print("\nNo valid metadata found to export.")


def copy_files_incremental(processed_path: Path, regex_pattern: str, dest_folder: Path) -> dict:
    """Incrementally copy files matching regex pattern to destination."""
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
        if dest_file.exists():
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


def copy_matching_files(processed_path: Path, regex_pattern: str, dest_folder: Path):
    """Copy files matching regex pattern to destination."""
    dest_folder.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(regex_pattern)
    files_copied = 0

    for file in processed_path.iterdir():
        if not file.is_file():
            continue
        if file.suffix.lower() not in [".pdf", ".json"]:
            continue
        if pattern.search(file.name):
            shutil.copy2(file, dest_folder / file.name)
            files_copied += 1

    print(f"Copied {files_copied} files matching '{regex_pattern}' to {dest_folder}")


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

    all_passed = True
    check_results = []
    for idx, check in enumerate(checks):
        found = False
        for json_path, data in file_data:
            if all(str(data.get(k, "")).strip() == str(v).strip() for k, v in check.items()):
                found = True
                break
        check_results.append((found, idx, check))
        if not found:
            all_passed = False

    for found, idx, check in sorted(check_results, key=lambda x: (not x[0], x[1])):
        if found:
            print(f"[OK] {check} -- FOUND")
    for found, idx, check in sorted(check_results, key=lambda x: (not x[0], x[1])):
        if not found:
            print(f"[FAIL] {check} -- NOT FOUND")

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
    zip_passwords_file_path = str(Path.home() / ".documentor/passwords.txt")
    assert os.path.exists(zip_passwords_file_path), f"Missing zip passwords file: {zip_passwords_file_path}"

    processed_files_excel_path = Path(PROCESSED_FILES_DIR) / "processed_files.xlsx"
    raw_dirs = [p for p in RAW_FILES_DIR.split(';') if p]

    for rd in raw_dirs:
        run_step(f'mbox-extractor "{rd}"', "Step 1: Google Takeout mbox extraction")
        run_step(f'archive-extractor "{rd}" --passwords "{zip_passwords_file_path}"', "Step 2: Google Takeout zip extraction")

    raw_dirs_arg = ";".join(raw_dirs)
    run_step(f'"{sys.executable}" "{__file__}" extract_new "{PROCESSED_FILES_DIR}" --raw_path "{raw_dirs_arg}"', "Step 3: Extract new documents")
    run_step(f'"{sys.executable}" "{__file__}" rename_files "{PROCESSED_FILES_DIR}"', "Step 4: Rename files and metadata")
    run_step(f'"{sys.executable}" "{__file__}" export_excel "{PROCESSED_FILES_DIR}" --excel_output_path "{processed_files_excel_path}"', "Step 5: Export metadata to Excel")
    run_step(f'"{sys.executable}" "{__file__}" copy_matching "{PROCESSED_FILES_DIR}" --regex_pattern "{export_date}" --copy_dest_folder "{export_date_dir}"', "Step 6: Copy matching documents")
    run_step(f'pdf-merger "{export_date_dir}"', "Step 7: Merge PDFs")
    run_step(f'"{sys.executable}" "{__file__}" check_files_exist "{export_date_dir}"', "Step 8: Validate exported files")

    print("All steps completed successfully.")


# ------------------- TASK HANDLERS -------------------

def _task__extract_new(processed_path, raw_paths):
    """Extract and classify new PDF files."""
    global failure_logger
    log_path = processed_path / "classification_failures.log"
    failure_logger = setup_failure_logger(log_path)
    print(f"Logging failures to: {log_path}")

    print("Building hash index from metadata files...")
    known_hashes = set(build_hash_index(processed_path).keys())

    print("Scanning for new PDFs...")
    pdf_paths = find_pdf_files(raw_paths)
    print(f"Found {len(pdf_paths)} PDFs in raw directories")

    print(f"Stage 1: Quick filtering using fast file hashes...")
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
        rename_pdf_files(files_to_process, content_hash_map, known_hashes, processed_path)

    print("Extraction complete.")


def _task__rename_files(processed_path):
    """Rename existing PDF files based on metadata."""
    print("Renaming existing PDF files and metadata based on metadata...")

    json_files = list(processed_path.rglob("*.json"))
    valid_entries = []

    for metadata_path in tqdm(json_files, desc="Validating metadata"):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = DocumentMetadata.model_validate(data)

            pdf_path = metadata_path.with_suffix(".pdf")
            if not pdf_path.exists():
                print(f"Skipping {metadata_path.name}: PDF file not found")
                continue

            valid_entries.append((pdf_path, metadata))
        except Exception as e:
            print(f"Skipping {metadata_path.name}: {e}")

    print(f"Found {len(valid_entries)} files to rename")

    renamed_count = 0
    for old_pdf_path, metadata in valid_entries:
        content_hash = metadata.content_hash
        new_filename = file_name_from_metadata(metadata, content_hash)
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


def _task__validate_metadata(processed_path):
    """Validate metadata and PDFs."""
    print("Validating existing metadata and PDFs...")
    validate_metadata(processed_path)
    print("Validation complete.")


def _task__export_excel(processed_path, excel_output_path):
    """Export metadata to Excel."""
    print("Exporting metadata to Excel...")
    export_metadata_to_excel(processed_path, excel_output_path)
    print("Excel export complete.")


def _task__copy_matching(processed_path, regex_pattern, copy_dest_folder):
    """Copy files matching a regex pattern."""
    if not regex_pattern or not copy_dest_folder:
        print("For 'copy_matching', --regex_pattern and --copy_dest_folder are required.")
        return
    copy_matching_files(processed_path, regex_pattern, Path(copy_dest_folder))
    print("Copy-matching complete.")


def _task__export_all_dates(processed_path, export_base_dir, run_merge=False):
    """Export files for all unique dates found in processed files."""
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

        stats = copy_files_incremental(processed_path, date, export_date_dir)
        total_copied += stats['copied']
        total_skipped += stats['skipped']

        if stats['total'] == 0:
            print(f"  No files match date pattern '{date}'")
        else:
            print(f"  Copied: {stats['copied']}, Skipped: {stats['skipped']}, Total: {stats['total']}")

        if stats['copied'] > 0:
            changed_directories.append(export_date_dir)
        elif stats['total'] > 0:
            if export_date_dir.exists() and directory_has_changed(export_date_dir):
                changed_directories.append(export_date_dir)

    print(f"\n=== Summary ===")
    print(f"Processed {len(all_dates)} date(s)")
    print(f"Total files copied: {total_copied}")
    print(f"Total files skipped (unchanged): {total_skipped}")
    print(f"Directories with changes: {len(changed_directories)}")

    if run_merge and changed_directories:
        print(f"\n=== Running PDF Merge ===")
        from shutil import which
        if which("pdf-merger") is None:
            print("WARNING: pdf-merger tool not found in PATH. Skipping merge step.")
        else:
            for export_dir in changed_directories:
                print(f"\nMerging PDFs in {export_dir}...")
                result = subprocess.run(f'pdf-merger "{export_dir}"', shell=True, text=True)
                if result.returncode == 0:
                    print(f"  Merge completed successfully")
                else:
                    print(f"  Merge failed with exit code {result.returncode}")

    print("\nExport all dates complete.")


def _task__check_files_exist(processed_path, check_schema_path):
    """Check that expected files exist."""
    if not check_schema_path:
        print("For 'check_files_exist', --check_schema_path is required.")
        return
    check_files_exist(processed_path, Path(check_schema_path))
    print("File existence check complete.")


def process_folder(task: str, processed_path: str, raw_paths=None, excel_output_path: str = None,
                   regex_pattern: str = None, copy_dest_folder: str = None, check_schema_path: str = None,
                   export_base_dir: str = None, run_merge: bool = False):
    """Dispatch to appropriate task handler."""
    if raw_paths is not None:
        raw_paths = [Path(p) for p in raw_paths]
    processed_path = Path(processed_path)
    processed_path.mkdir(parents=True, exist_ok=True)

    if task == "extract_new":
        _task__extract_new(processed_path, raw_paths)
    elif task == "rename_files":
        _task__rename_files(processed_path)
    elif task == "validate_metadata":
        _task__validate_metadata(processed_path)
    elif task == "export_excel":
        _task__export_excel(processed_path, excel_output_path)
    elif task == "copy_matching":
        _task__copy_matching(processed_path, regex_pattern, copy_dest_folder)
    elif task == "export_all_dates":
        _task__export_all_dates(processed_path, export_base_dir, run_merge)
    elif task == "check_files_exist":
        _task__check_files_exist(processed_path, check_schema_path)
    else:
        print("Invalid task specified.")


# ------------------- MAIN -------------------

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(description="Process a folder of PDF files.")
    parser.add_argument("task", type=str, choices=[
        'extract_new', 'rename_files', 'validate_metadata', 'export_excel',
        'copy_matching', 'export_all_dates', 'check_files_exist', 'pipeline'
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
    args = parser.parse_args()

    if args.task == "pipeline":
        if args.export_date and not re.match(r"^\d{4}-\d{2}$", args.export_date):
            parser.error("The --export_date argument must be in YYYY-MM format.")
        pipeline(export_date_arg=args.export_date)
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
    if args.task == "check_files_exist":
        if not check_schema_path:
            check_schema_path = str(Path.home() / ".documentor" / "file_check_validations.json")
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


if __name__ == "__main__":
    main()
