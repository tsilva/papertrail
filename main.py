from dotenv import load_dotenv
load_dotenv(override=True)

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

# ------------------- CONFIG -------------------

ANTHROPIC_MODEL_ID = os.getenv("ANTHROPIC_MODEL_ID")

# ------------------- ENUMS & MODELS -------------------

import importlib.resources

def load_document_types():
    # Try to load from config/document_types.json relative to the script location (works for both pipx and dev)
    candidates = [
        Path(__file__).parent / "config" / "document_types.json",  # dev and editable install
        Path(sys.argv[0]).parent / "config" / "document_types.json",  # pipx global bin
        Path.cwd() / "config" / "document_types.json",  # user runs from project root
    ]
    for path in candidates:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    raise FileNotFoundError(
        "Could not find 'config/document_types.json'. "
        "Make sure the config directory is present next to the script or in your working directory."
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
        for file in files if file.lower().endswith('.pdf')
    ]

# ------------------- CLASSIFICATION -------------------

def classify_pdf_document(pdf_path: Path, file_hash: str) -> DocumentMetadata:
    client = anthropic.Anthropic()

    try:
        # Use PyMuPDF to render the first page as an image
        doc = fitz.open(str(pdf_path))
        page = doc[0]  # First page
        pix = page.get_pixmap()  # Render to pixmap
        img_buffer = io.BytesIO(pix.tobytes("jpeg"))  # Convert to JPEG in memory
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")
        doc.close()  # Close the document
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
        
        print("1")
        tool_result = next((c.input for c in response.content if hasattr(c, "input")), None)
        if not tool_result:
            raise ValueError("Claude did not return structured classification.")
        
        print("2")
        metadata = DocumentMetadata.model_validate(tool_result)
        metadata.hash = file_hash
        
        print("3")
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
        print(f"Processed: {pdf_path.name} → {filename}")
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
            print(f"Renamed: {old_pdf_path.name} → {new_filename}")
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
                # Handle cases where date is missing or malformed
                metadata_dict["year"] = None
                metadata_dict["month"] = None

            metadata_list.append(metadata_dict)
        except Exception as e:
            print(f"Skipping {metadata_path.name}: {e}")

    if metadata_list:
        df = pd.DataFrame(metadata_list)
        # Optional: Reorder to put filename, length, year, month first
        base_cols = ["filename", "filename_length", "year", "month"]
        other_cols = [col for col in df.columns if col not in base_cols]
        df = df[base_cols + other_cols]

        # Use ExcelWriter to gain access to the worksheet object
        with pd.ExcelWriter(excel_output_path, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Sheet1')

            # Access the workbook and worksheet objects
            worksheet = writer.sheets['Sheet1']

            # Freeze the top row (row 1)
            worksheet.freeze_panes = 'A2'

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

# ------------------- MAIN -------------------

def process_folder(task: str, processed_path: str, raw_path: str = None, excel_output_path: str = None, regex_pattern: str = None, copy_dest_folder: str = None):
    if raw_path is not None: raw_path = Path(raw_path)
    processed_path = Path(processed_path)
    processed_path.mkdir(parents=True, exist_ok=True)

    if task == "extract":
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

    elif task == "rename":
        print("Renaming existing PDF files and metadata based on metadata...")
        rename_existing_files(processed_path)
        print("Renaming complete.")

    elif task == "validate":
        print("Validating existing metadata and PDFs...")
        _ = validate_metadata(processed_path)
        print("Validation complete.")

    elif task == "excel":
        print("Exporting metadata to Excel...")
        export_metadata_to_excel(processed_path, excel_output_path)
        print("Excel export complete.")

    elif task == "copy-matching":
        if not regex_pattern or not copy_dest_folder:
            print("For 'copy-matching', --regex_pattern and --copy_dest_folder are required.")
            return
        copy_matching_files(processed_path, regex_pattern, Path(copy_dest_folder))
        print("Copy-matching complete.")

    else:
        print("Invalid task specified. Use 'extract', 'rename', 'validate', 'excel', or 'copy-matching'.")

def main():
    parser = argparse.ArgumentParser(description="Process a folder of PDF files.")
    parser.add_argument("task", type=str, choices=['extract', 'rename', 'validate', 'excel', 'copy-matching'], help="Specify task: 'extract', 'rename', 'validate', 'excel', or 'copy-matching'.")
    parser.add_argument("processed_path", type=str, help="Path to output folder.")
    parser.add_argument("--raw_path", type=str, help="Path to documents folder (required for 'extract' task).")
    parser.add_argument("--excel_output_path", type=str, help="Path to output Excel file (for 'excel' task).")
    parser.add_argument("--regex_pattern", type=str, help="Regex pattern for matching filenames (for 'copy-matching' task).")
    parser.add_argument("--copy_dest_folder", type=str, help="Destination folder for copied files (for 'copy-matching' task).")
    args = parser.parse_args()

    if not os.path.exists(args.processed_path): parser.error(f"The processed_path '{args.processed_path}' does not exist.")
    if not os.path.isdir(args.processed_path): parser.error(f"The processed_path '{args.processed_path}' is not a directory.")

    if args.task == "extract":
        if not args.raw_path: parser.error("the --raw_path argument is required when task is 'extract'.")
        if not os.path.exists(args.raw_path): parser.error(f"The raw_path '{args.raw_path}' does not exist.")
        if not os.path.isdir(args.raw_path): parser.error(f"The raw_path '{args.raw_path}' is not a directory.")

    if args.task == "excel":
        if not args.excel_output_path: parser.error("the --excel_output_path argument is required when task is 'excel'.")
        if not args.excel_output_path.endswith(".xlsx"): parser.error("the --excel_output_path argument must end with '.xlsx'.")

    if args.task == "copy-matching":
        if not args.regex_pattern: parser.error("the --regex_pattern argument is required when task is 'copy-matching'.")
        if not args.copy_dest_folder: parser.error("the --copy_dest_folder argument is required when task is 'copy-matching'.")
        if not os.path.exists(args.copy_dest_folder):
            os.makedirs(args.copy_dest_folder, exist_ok=True)
        if not os.path.isdir(args.copy_dest_folder): parser.error(f"The copy_dest_folder '{args.copy_dest_folder}' is not a directory.")

    process_folder(
        args.task,
        args.processed_path,
        raw_path=args.raw_path,
        excel_output_path=args.excel_output_path,
        regex_pattern=args.regex_pattern,
        copy_dest_folder=args.copy_dest_folder
    )

if __name__ == "__main__":
    main()