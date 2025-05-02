import os
import re
import io
import json
import base64
import shutil
import hashlib
import unicodedata
import argparse
from enum import Enum
from pathlib import Path
from typing import Optional

import sys
import anthropic
import fitz
import pandas as pd
from tqdm import tqdm
from pydantic import BaseModel, Field, field_validator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# ------------------- CONFIG -------------------

def get_config_dir_and_env_path():
    config_dir = Path.home() / ".documentor"
    env_path = config_dir / ".env"
    return config_dir, env_path

def ensure_home_config_and_env():
    config_dir, env_path = get_config_dir_and_env_path()
    config_dir.mkdir(parents=True, exist_ok=True)

    # Ensure all config example files are present in home config dir
    config_example_dir = Path(__file__).parent / "config"
    files_copied = []
    if config_example_dir.exists():
        for file in config_example_dir.iterdir():
            if file.is_file() and file.name.endswith('.example'):
                dest_name = file.name[:-8]
                dest = config_dir / dest_name
                if not dest.exists():
                    shutil.copy(file, dest)
                    files_copied.append(dest_name)
    if files_copied:
        print(f"✅ Copied example config files to {config_dir}: {', '.join(files_copied)}.\nEdit these files before rerunning.")
        sys.exit(0)
    # Always ensure .env exists (even if not in example files)
    if not env_path.exists():
        env_path.touch()
        print(f"✅ Created .env at {env_path}. Edit this file before rerunning.")
        sys.exit(0)
    return config_dir, env_path

# Always load .env from ~/.documentor/.env, create if missing
CONFIG_DIR, ENV_PATH = ensure_home_config_and_env()

from dotenv import load_dotenv
load_dotenv(dotenv_path=ENV_PATH, override=True)

ANTHROPIC_MODEL_ID = os.getenv("ANTHROPIC_MODEL_ID")

# ------------------- ENUMS & MODELS -------------------

import importlib.resources

def load_document_types():
    # Always check home config dir first, then fallback to other locations
    candidates = [
        Path.home() / ".documentor" / "document_types.json",  # home config dir
        Path(__file__).parent / "config" / "document_types.json",  # dev and editable install
        Path(sys.argv[0]).parent / "config" / "document_types.json",  # pipx global bin
        Path.cwd() / "config" / "document_types.json",  # user runs from project root
    ]
    for path in candidates:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError(
        "Could not find 'document_types.json' in '~/.documentor', 'config', or working directory. "
        "Make sure the config file exists in your home config folder or project."
    )

DOCUMENT_TYPES = load_document_types()

def create_dynamic_enum(name, data):
    return Enum(name, dict([(k, k) for k in data]), type=str)

DocumentType = create_dynamic_enum('DocumentType', DOCUMENT_TYPES)

class DocumentMetadata(BaseModel):
    issue_date: str = Field(description="Date issued, format: YYYY-MM-DD.", example="2025-01-02")
    document_type: DocumentType = Field(description="Type of document.", example="invoice")
    issuing_party: str = Field(description="Issuer name, one word if possible.", example="Amazon")
    service_name: Optional[str] = Field(description="Product/service name if applicable (as short as possible).", example="Youtube Premium")
    total_amount: Optional[float] = Field(default=None, description="Total currency amount.")
    total_amount_currency: Optional[str] = Field(description="Currency of the total amount.", example="EUR")
    confidence: float = Field(description="Confidence score between 0 and 1.")
    reasoning: str = Field(description="Why this classification was chosen.")
    hash: str = Field(description="SHA256 hash of the file (first 8 chars).", example="a1b2c3d4")
    create_date: Optional[str] = Field(description="Date this metadata was created, format: YYYY-MM-DD.", example="2024-06-01")

    @field_validator('total_amount', mode='before')
    @classmethod
    def clean_and_validate_amount(cls, value):
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            value = re.sub(r'[^\d,.-]', '', value).replace('.', '').replace(',', '.')
            return float(value)
        raise ValueError(f"Invalid type for amount: {type(value)}")

    @field_validator('total_amount_currency', mode='before')
    @classmethod
    def normalize_currency(cls, value):
        if value is None:
            return None
        value = value.strip().upper()
        return {
            '€': 'EUR', 'EURO': 'EUR',
            '$': 'USD',
            '£': 'GBP'
        }.get(value, value)

# ------------------- CLAUDE TOOL SETUP -------------------

TOOLS = [{
    "name": "extract_document_metadata",
    "description": "Extract metadata from a document.",
    "input_schema": DocumentMetadata.model_json_schema()
}]

# ------------------- UTILS -------------------

def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b''): h.update(chunk)
    return h.hexdigest()[:8]

def build_output_hash_index(output_path: Path) -> dict:
    hash_index = {}
    for root, _, files in os.walk(output_path):
        for file in files:
            if not file.lower().endswith(".json"):
                continue
            with open(Path(root) / file, "r", encoding="utf-8") as f:
                metadata = json.load(f)
                hash_index[metadata.get('hash')] = Path(root) / file.replace(".json", ".pdf")
    return hash_index

def sanitize_filename_component(s: str) -> str:
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[\\/*?:"<>|()\[\],]', '', s).strip()
    return re.sub(r'\s+', ' ', s)

def file_name_from_metadata(metadata: DocumentMetadata, file_hash: str) -> str:
    parts = [
        sanitize_filename_component(metadata.issue_date),
        sanitize_filename_component(metadata.document_type.value),
        sanitize_filename_component(metadata.issuing_party)
    ]

    if metadata.service_name:
        parts.append(sanitize_filename_component(metadata.service_name))

    if metadata.total_amount is not None:
        amount = f"{metadata.total_amount:.0f}" if metadata.total_amount.is_integer() else f"{metadata.total_amount:.2f}"
        currency = metadata.total_amount_currency or ""
        parts.append(sanitize_filename_component(f"{amount} {currency}".strip()))

    parts.append(f"{file_hash}.pdf")
    return " - ".join(parts).lower()

def find_pdf_files(folder_path: Path):
    return [
        Path(root) / file
        for root, _, files in os.walk(folder_path)
        for file in files
        if file.lower().endswith('.pdf')
        and not file.startswith('.')
        and (Path(root) / file).stat().st_size > 0
    ]

# ------------------- CLASSIFICATION -------------------

def classify_pdf_document(pdf_path: Path, file_hash: str) -> DocumentMetadata:
    client = anthropic.Anthropic()

    try:
        # Use PyMuPDF to render the first page as an image
        doc = fitz.open(str(pdf_path))
        page = doc[0]  # First page
        pix = page.get_pixmap()  # Render to pixmap

        # --- Boost contrast using PIL ---
        from PIL import Image, ImageEnhance
        img = Image.open(io.BytesIO(pix.tobytes("jpeg")))
        enhancer = ImageEnhance.Contrast(img)
        img_enhanced = enhancer.enhance(2.0)  # 2.0 = double contrast, adjust as needed

        img_buffer = io.BytesIO()
        img_enhanced.save(img_buffer, format="JPEG")
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")
        doc.close()  # Close the document
        # --- end contrast boost ---
    except Exception as e:
        print(e)
        raise RuntimeError(f"Failed to render PDF image: {pdf_path}") from e

    try:
        response = client.messages.create(
            model=ANTHROPIC_MODEL_ID,
            max_tokens=4096,
            temperature=0,
            system=[{
                "type": "text",
                "text": "You are a document classification assistant. Use layout, structure, and content to determine type.",
                "cache_control": {"type": "ephemeral"}
            }],
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": "What type of document is this? Use the structured tool."},
                    {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img_b64}}
                ]
            }],
            tools=TOOLS
        )
        
        tool_result = next((c.input for c in response.content if hasattr(c, "input")), None)
        if not tool_result:
            raise ValueError("Claude did not return structured classification.")
        
        metadata = DocumentMetadata.model_validate(tool_result)
        metadata.hash = file_hash
        metadata.create_date = datetime.now().strftime("%Y-%m-%d")
        return metadata
    except Exception as e:
        print(e)
        raise RuntimeError(f"Classification failed for: {pdf_path}") from e

# ------------------- RENAMING & PROCESSING -------------------

def rename_single_pdf(pdf_path: Path, file_hash: str, processed_path: Path, known_hashes: set):
    try:
        metadata = classify_pdf_document(pdf_path, file_hash)
        filename = file_name_from_metadata(metadata, file_hash)
        new_pdf_path = processed_path / filename

        shutil.copy2(pdf_path, new_pdf_path)

        with open(new_pdf_path.with_suffix('.json'), "w", encoding="utf-8") as f:
            json.dump(metadata.model_dump(), f, indent=4)

        known_hashes.add(file_hash)
        print(f"Processed: {pdf_path.name} -> {filename}")
    except Exception as e:
        print(e)
        print(f"Failed to process {pdf_path.name}: {e}")

def rename_pdf_files(pdf_paths, file_hash_map, known_hashes, processed_path, max_workers=4):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(tqdm(
            executor.map(
                lambda p: rename_single_pdf(p, file_hash_map[p], processed_path, known_hashes),
                pdf_paths
            ),
            total=len(pdf_paths)
        ))

def validate_metadata(output_path: Path):
    valid_entries = []
    errors = []
    json_files = list(output_path.rglob("*.json"))

    for metadata_path in tqdm(json_files, desc="Validating metadata"):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = DocumentMetadata.model_validate(data)

            file_hash = metadata.hash
            if not file_hash:
                raise ValueError("Missing 'hash' in metadata.")

            pdf_path = metadata_path.with_suffix(".pdf")
            if not pdf_path.exists():
                raise FileNotFoundError(f"Missing PDF for metadata: {pdf_path.name}")

            actual_hash = hash_file(pdf_path)
            if file_hash != actual_hash:
                raise ValueError(f"Hash mismatch: metadata hash is '{file_hash}', actual is '{actual_hash}'.")

            if file_hash not in pdf_path.name:
                raise ValueError(f"Filename '{pdf_path.name}' does not include the expected hash '{file_hash}'.")

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
    valid_entries = validate_metadata(output_path)

    for old_pdf_path, metadata in valid_entries:
        file_hash = metadata.hash
        new_filename = file_name_from_metadata(metadata, file_hash)
        new_pdf_path = output_path / new_filename
        new_metadata_path = new_pdf_path.with_suffix(".json")

        # Skip if the filename hasn't changed
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
    metadata_list = []
    json_files = list(processed_path.rglob("*.json"))

    for metadata_path in tqdm(json_files, desc="Collecting metadata"):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = DocumentMetadata.model_validate(data)
            metadata_dict = metadata.model_dump()

            # Remove 'reasoning' field
            metadata_dict.pop("reasoning", None)

            # Add filename (corresponding PDF)
            pdf_path = metadata_path.with_suffix(".pdf")
            filename = pdf_path.name if pdf_path.exists() else ""
            metadata_dict["filename"] = filename

            # Add filename length
            metadata_dict["filename_length"] = len(filename)

            # Extract year and month from issue_date
            try:
                # Assuming YYYY-MM-DD format
                date_parts = metadata.issue_date.split('-')
                metadata_dict["year"] = int(date_parts[0])
                metadata_dict["month"] = int(date_parts[1])
            except (IndexError, ValueError, AttributeError):
                metadata_dict["year"] = None
                metadata_dict["month"] = None

            # Ensure document_type is just the value, not Enum repr
            if isinstance(metadata_dict.get("document_type"), Enum):
                metadata_dict["document_type"] = metadata_dict["document_type"].value
            elif (
                isinstance(metadata_dict.get("document_type"), str)
                and metadata_dict["document_type"].startswith("DocumentType.")
            ):
                metadata_dict["document_type"] = metadata_dict["document_type"].split(".", 1)[-1]

            metadata_list.append(metadata_dict)
        except Exception as e:
            print(f"Skipping {metadata_path.name}: {e}")

    if metadata_list:
        df = pd.DataFrame(metadata_list)
        # Set column order as requested
        ordered_cols = [
            "confidence",
            "issue_date",
            "year",
            "month",
            "hash",
            "filename",
            "filename_length",
            "document_type",
            "issuing_party",
            "service_name",
            "total_amount",
            "total_amount_currency"
        ]
        # Add any extra columns at the end (if present)
        extra_cols = [col for col in df.columns if col not in ordered_cols]
        df = df[ordered_cols + extra_cols]

        # Sort by issue_date descending (most recent first)
        if "issue_date" in df.columns:
            df = df.sort_values(by="issue_date", ascending=False)

        # Use ExcelWriter to gain access to the worksheet object
        with pd.ExcelWriter(excel_output_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Sheet1')

            worksheet = writer.sheets['Sheet1']
            worksheet.freeze_panes = 'A2'

            # Snap requested columns' width to content width
            for col in ordered_cols:
                if col in df.columns:
                    col_idx = df.columns.get_loc(col) + 1  # openpyxl is 1-based
                    max_len = max(
                        [len(str(val)) if val is not None else 0 for val in df[col].values] + [len(col)]
                    )
                    max_len = min(max_len, 100)  # Truncate to max 100
                    worksheet.column_dimensions[chr(64 + col_idx)].width = max_len + 2

            # Hide specified columns by default
            hidden_cols = ["year", "month", "filename_length"]
            for col in hidden_cols:
                if col in df.columns:
                    col_idx = df.columns.get_loc(col) + 1  # openpyxl is 1-based
                    worksheet.column_dimensions[chr(64 + col_idx)].hidden = True

        print(f"\nExported {len(df)} entries to {excel_output_path}")
    else:
        print("\nNo valid metadata found to export.")

def copy_matching_files(processed_path: Path, regex_pattern: str, dest_folder: Path):
    """
    Copy all PDF and JSON files in processed_path whose filenames match regex_pattern to dest_folder.
    """
    dest_folder.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(regex_pattern)
    files_copied = 0

    for file in processed_path.iterdir():
        if not file.is_file():
            continue
        if not (file.suffix.lower() in [".pdf", ".json"]):
            continue
        if pattern.search(file.name):
            shutil.copy2(file, dest_folder / file.name)
            files_copied += 1

    print(f"Copied {files_copied} files matching '{regex_pattern}' to {dest_folder}")

def check_files_exist(target_folder: Path, validation_schema_path: Path):
    """
    For each entry in the validation schema, check if there is at least one .json file in the target folder
    whose contents match all key/value pairs in the entry.
    """
    # Load validation schema
    with open(validation_schema_path, "r", encoding="utf-8") as f:
        checks = json.load(f)

    # Load all .json files in target_folder
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

    # Print OKs first, then FAILs
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

# ------------------- MAIN -------------------

import subprocess

def run_step(cmd, step_desc):
    print(f"### {step_desc}...")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr)
    if result.returncode != 0:
        print(f"{step_desc} failed with exit code {result.returncode}.")
        sys.exit(result.returncode)
    print(f"### {step_desc}... Finished.")

def pipeline(export_date_arg=None):
    from shutil import which
    from datetime import datetime, timedelta

    # Validate required vars
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

    # Assert required external commands are available
    for tool in ["mbox-extractor", "archive-extractor", "pdf-merger"]:
        if which(tool) is None:
            print(f"Required tool '{tool}' not found in PATH. Please install it and try again.")
            sys.exit(1)

    # Use export_date_arg if provided, else default to previous month
    if export_date_arg:
        export_date = export_date_arg
    else:
        today = datetime.now()
        first_of_this_month = today.replace(day=1)
        last_month = first_of_this_month - timedelta(days=1)
        export_date = last_month.strftime("%Y-%m")

    # Validate export_date format here
    if not re.match(r"^\d{4}-\d{2}$", export_date):
        print("The export_date must be in YYYY-MM format.")
        sys.exit(1)

    export_date_dir = os.path.join(EXPORT_FILES_DIR, export_date)

    zip_passwords_file_path = str(Path.home() / ".documentor/passwords.txt")
    assert os.path.exists(zip_passwords_file_path), f"Missing zip passwords file: {zip_passwords_file_path}"

    processed_files_excel_path = Path(PROCESSED_FILES_DIR) / "processed_files.xlsx"

    # Step 1: mbox-extractor
    run_step(
        f'mbox-extractor "{RAW_FILES_DIR}"',
        "Step 1: Google Takeout mbox extraction"
    )

    # Step 2: archive-extractor
    run_step(
        f'archive-extractor "{RAW_FILES_DIR}" --passwords "{zip_passwords_file_path}"',
        "Step 2: Google Takeout zip extraction"
    )

    # Step 3: documentor extract_new
    run_step(
        f'"{sys.executable}" "{__file__}" extract_new "{PROCESSED_FILES_DIR}" --raw_path "{RAW_FILES_DIR}"',
        "Step 3: Extract new documents"
    )

    # Step 4: documentor rename_files
    run_step(
        f'"{sys.executable}" "{__file__}" rename_files "{PROCESSED_FILES_DIR}"',
        "Step 4: Rename files and metadata"
    )

    # Step 5: documentor export_excel
    run_step(
        f'"{sys.executable}" "{__file__}" export_excel "{PROCESSED_FILES_DIR}" --excel_output_path "{processed_files_excel_path}"',
        "Step 5: Export metadata to Excel"
    )

    # Step 6: documentor copy_matching
    run_step(
        f'"{sys.executable}" "{__file__}" copy_matching "{PROCESSED_FILES_DIR}" --regex_pattern "{export_date}" --copy_dest_folder "{export_date_dir}"',
        "Step 6: Copy matching documents"
    )

    # Step 7: pdf-merger
    run_step(
        f'pdf-merger "{export_date_dir}"',
        "Step 7: Merge PDFs"
    )

    # Step 8: documentor check_files_exist
    run_step(
        f'"{sys.executable}" "{__file__}" check_files_exist "{export_date_dir}"',
        "Step 8: Validate exported files"
    )

    print("All steps completed successfully.")

def _task__extract_new(processed_path, raw_path):
    print("Building hash index from metadata files...")
    known_hashes = set(build_output_hash_index(processed_path).keys())  # Convert to set for efficiency

    print("Scanning for new PDFs...")
    pdf_paths = find_pdf_files(raw_path)

    print(f"Calculating hashes for {len(pdf_paths)} PDFs...")
    file_hash_map = {pdf: hash_file(pdf) for pdf in tqdm(pdf_paths, desc="Hashing files")}
    files_to_process = [pdf for pdf in pdf_paths if file_hash_map[pdf] not in known_hashes]

    print(f"Found {len(files_to_process)} new PDFs.")
    rename_pdf_files(files_to_process, file_hash_map, known_hashes, processed_path)
    print("Extraction complete.")

def _task__rename_files(processed_path):
    print("Renaming existing PDF files and metadata based on metadata...")
    valid_entries = validate_metadata(processed_path)

    for old_pdf_path, metadata in valid_entries:
        file_hash = metadata.hash
        new_filename = file_name_from_metadata(metadata, file_hash)
        new_pdf_path = processed_path / new_filename
        new_metadata_path = new_pdf_path.with_suffix(".json")

        # Skip if the filename hasn't changed
        if old_pdf_path == new_pdf_path:
            continue

        try:
            old_metadata_path = old_pdf_path.with_suffix(".json")
            shutil.move(old_pdf_path, new_pdf_path)
            shutil.move(old_metadata_path, new_metadata_path)
            print(f"Renamed: {old_pdf_path.name} -> {new_filename}")
        except Exception as e:
            print(f"Failed to rename {old_pdf_path.name}: {e}")
    print("Renaming complete.")

def _task__validate_metadata(processed_path):
    print("Validating existing metadata and PDFs...")
    valid_entries = []
    errors = []
    json_files = list(processed_path.rglob("*.json"))

    for metadata_path in tqdm(json_files, desc="Validating metadata"):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = DocumentMetadata.model_validate(data)

            file_hash = metadata.hash
            if not file_hash:
                raise ValueError("Missing 'hash' in metadata.")

            pdf_path = metadata_path.with_suffix(".pdf")
            if not pdf_path.exists():
                raise FileNotFoundError(f"Missing PDF for metadata: {pdf_path.name}")

            actual_hash = hash_file(pdf_path)
            if file_hash != actual_hash:
                raise ValueError(f"Hash mismatch: metadata hash is '{file_hash}', actual is '{actual_hash}'.")

            if file_hash not in pdf_path.name:
                raise ValueError(f"Filename '{pdf_path.name}' does not include the expected hash '{file_hash}'.")

            valid_entries.append((pdf_path, metadata))

        except Exception as e:
            errors.append((metadata_path, str(e)))

    if errors:
        print("\nValidation errors found:")
        for meta_path, err in errors:
            print(f"- {meta_path}: {err}")
    else:
        print("\nAll metadata files passed validation.")

    print("Validation complete.")

def _task__export_excel(processed_path, excel_output_path):
    print("Exporting metadata to Excel...")
    metadata_list = []
    json_files = list(processed_path.rglob("*.json"))

    for metadata_path in tqdm(json_files, desc="Collecting metadata"):
        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            metadata = DocumentMetadata.model_validate(data)
            metadata_dict = metadata.model_dump()

            # Remove 'reasoning' field
            metadata_dict.pop("reasoning", None)

            # Add filename (corresponding PDF)
            pdf_path = metadata_path.with_suffix(".pdf")
            filename = pdf_path.name if pdf_path.exists() else ""
            metadata_dict["filename"] = filename

            # Add filename length
            metadata_dict["filename_length"] = len(filename)

            # Extract year and month from issue_date
            try:
                # Assuming YYYY-MM-DD format
                date_parts = metadata.issue_date.split('-')
                metadata_dict["year"] = int(date_parts[0])
                metadata_dict["month"] = int(date_parts[1])
            except (IndexError, ValueError, AttributeError):
                metadata_dict["year"] = None
                metadata_dict["month"] = None

            # Ensure document_type is just the value, not Enum repr
            if isinstance(metadata_dict.get("document_type"), Enum):
                metadata_dict["document_type"] = metadata_dict["document_type"].value
            elif (
                isinstance(metadata_dict.get("document_type"), str)
                and metadata_dict["document_type"].startswith("DocumentType.")
            ):
                metadata_dict["document_type"] = metadata_dict["document_type"].split(".", 1)[-1]

            metadata_list.append(metadata_dict)
        except Exception as e:
            print(f"Skipping {metadata_path.name}: {e}")

    if metadata_list:
        df = pd.DataFrame(metadata_list)
        # Set column order as requested
        ordered_cols = [
            "confidence",
            "issue_date",
            "year",
            "month",
            "hash",
            "filename",
            "filename_length",
            "document_type",
            "issuing_party",
            "service_name",
            "total_amount",
            "total_amount_currency"
        ]
        # Add any extra columns at the end (if present)
        extra_cols = [col for col in df.columns if col not in ordered_cols]
        df = df[ordered_cols + extra_cols]

        # Sort by issue_date descending (most recent first)
        if "issue_date" in df.columns:
            df = df.sort_values(by="issue_date", ascending=False)

        # Use ExcelWriter to gain access to the worksheet object
        with pd.ExcelWriter(excel_output_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Sheet1')

            worksheet = writer.sheets['Sheet1']
            worksheet.freeze_panes = 'A2'

            # Snap requested columns' width to content width
            for col in ordered_cols:
                if col in df.columns:
                    col_idx = df.columns.get_loc(col) + 1  # openpyxl is 1-based
                    max_len = max(
                        [len(str(val)) if val is not None else 0 for val in df[col].values] + [len(col)]
                    )
                    max_len = min(max_len, 100)  # Truncate to max 100
                    worksheet.column_dimensions[chr(64 + col_idx)].width = max_len + 2

            # Hide specified columns by default
            hidden_cols = ["year", "month", "filename_length"]
            for col in hidden_cols:
                if col in df.columns:
                    col_idx = df.columns.get_loc(col) + 1  # openpyxl is 1-based
                    worksheet.column_dimensions[chr(64 + col_idx)].hidden = True

        print(f"\nExported {len(df)} entries to {excel_output_path}")
    else:
        print("\nNo valid metadata found to export.")
    print("Excel export complete.")

def _task__copy_matching(processed_path, regex_pattern, copy_dest_folder):
    if not regex_pattern or not copy_dest_folder:
        print("For 'copy_matching', --regex_pattern and --copy_dest_folder are required.")
        return
    dest_folder = Path(copy_dest_folder)
    dest_folder.mkdir(parents=True, exist_ok=True)
    pattern = re.compile(regex_pattern)
    files_copied = 0

    for file in processed_path.iterdir():
        if not file.is_file():
            continue
        if not (file.suffix.lower() in [".pdf", ".json"]):
            continue
        if pattern.search(file.name):
            shutil.copy2(file, dest_folder / file.name)
            files_copied += 1

    print(f"Copied {files_copied} files matching '{regex_pattern}' to {dest_folder}")
    print("Copy-matching complete.")

def _task__check_files_exist(processed_path, check_schema_path):
    if not check_schema_path:
        print("For 'check_files_exist', --check_schema_path is required.")
        return
    target_folder = processed_path
    validation_schema_path = Path(check_schema_path)
    # ...existing code for check_files_exist...
    # Inline the logic from check_files_exist
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

    # Print OKs first, then FAILs
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
    print("File existence check complete.")

def process_folder(task: str, processed_path: str, raw_path: str = None, excel_output_path: str = None, regex_pattern: str = None, copy_dest_folder: str = None, check_schema_path: str = None):
    if raw_path is not None: raw_path = Path(raw_path)
    processed_path = Path(processed_path)
    processed_path.mkdir(parents=True, exist_ok=True)

    if task == "extract_new": _task__extract_new(processed_path, raw_path)
    elif task == "rename_files": _task__rename_files(processed_path)
    elif task == "validate_metadata": _task__validate_metadata(processed_path)
    elif task == "export_excel": _task__export_excel(processed_path, excel_output_path)
    elif task == "copy_matching": _task__copy_matching(processed_path, regex_pattern, copy_dest_folder)
    elif task == "check_files_exist": _task__check_files_exist(processed_path, check_schema_path)
    else:
        print("Invalid task specified. Use 'extract_new', 'rename_files', 'validate_metadata', 'export_excel', 'copy_matching', or 'check_files_exist'.")

def main():
    parser = argparse.ArgumentParser(description="Process a folder of PDF files.")
    parser.add_argument("task", type=str, choices=[
        'extract_new', 'rename_files', 'validate_metadata', 'export_excel', 'copy_matching', 'check_files_exist', 'pipeline'
    ], help="Specify task: 'extract_new', 'rename_files', 'validate_metadata', 'export_excel', 'copy_matching', 'check_files_exist', or 'pipeline'.")
    parser.add_argument("processed_path", type=str, nargs='?', help="Path to output folder.")
    parser.add_argument("--raw_path", type=str, help="Path to documents folder (required for 'extract_new' task).")
    parser.add_argument("--excel_output_path", type=str, help="Path to output Excel file (for 'export_excel' task).")
    parser.add_argument("--regex_pattern", type=str, help="Regex pattern for matching filenames (for 'copy_matching' task).")
    parser.add_argument("--copy_dest_folder", type=str, help="Destination folder for copied files (for 'copy_matching' task).")
    parser.add_argument("--check_schema_path", type=str, help="Validation schema path (for 'check_files_exist' task).")
    parser.add_argument("--export_date", type=str, help="Export date in YYYY-MM format (for 'pipeline' task, optional).")
    args = parser.parse_args()

    if args.task == "pipeline":
        if args.export_date:
            import re
            if not re.match(r"^\d{4}-\d{2}$", args.export_date):
                parser.error("The --export_date argument must be in YYYY-MM format.")
        pipeline(export_date_arg=args.export_date)
        return

    if not args.processed_path: parser.error("the processed_path argument is required.")
    if not os.path.exists(args.processed_path): parser.error(f"The processed_path '{args.processed_path}' does not exist.")
    if not os.path.isdir(args.processed_path): parser.error(f"The processed_path '{args.processed_path}' is not a directory.")

    if args.task == "extract_new":
        if not args.raw_path: parser.error("the --raw_path argument is required when task is 'extract_new'.")
        if not os.path.exists(args.raw_path): parser.error(f"The raw_path '{args.raw_path}' does not exist.")
        if not os.path.isdir(args.raw_path): parser.error(f"The raw_path '{args.raw_path}' is not a directory.")

    if args.task == "export_excel":
        if not args.excel_output_path: parser.error("the --excel_output_path argument is required when task is 'export_excel'.")
        if not args.excel_output_path.endswith(".xlsx"): parser.error("the --excel_output_path argument must end with '.xlsx'.")

    if args.task == "copy_matching":
        if not args.regex_pattern: parser.error("the --regex_pattern argument is required when task is 'copy_matching'.")
        if not args.copy_dest_folder: parser.error("the --copy_dest_folder argument is required when task is 'copy_matching'.")
        if not os.path.exists(args.copy_dest_folder):
            os.makedirs(args.copy_dest_folder, exist_ok=True)
        if not os.path.isdir(args.copy_dest_folder): parser.error(f"The copy_dest_folder '{args.copy_dest_folder}' is not a directory.")

    if args.task == "check_files_exist":
        # Default check_schema_path if not provided
        check_schema_path = args.check_schema_path
        if not check_schema_path:
            check_schema_path = str(Path.home() / ".documentor" / "file_check_validations.json")
        if not os.path.exists(check_schema_path): parser.error(f"The check_schema_path '{check_schema_path}' does not exist.")

    process_folder(
        args.task,
        args.processed_path,
        raw_path=args.raw_path,
        excel_output_path=args.excel_output_path,
        regex_pattern=args.regex_pattern,
        copy_dest_folder=args.copy_dest_folder,
        check_schema_path=check_schema_path if args.task == "check_files_exist" else args.check_schema_path
    )

if __name__ == "__main__":
    main()