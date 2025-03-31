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

import anthropic
import pdf2image
from tqdm import tqdm
from pydantic import BaseModel, Field, field_validator
from concurrent.futures import ThreadPoolExecutor

# ------------------- CONFIG -------------------

ANTHROPIC_MODEL_ID = os.getenv("ANTHROPIC_MODEL_ID")

# ------------------- ENUMS & MODELS -------------------

with open("config/document_types.json", "r", encoding="utf-8") as f:
    DOCUMENT_TYPES = json.load(f)

def create_dynamic_enum(name, data):
    return Enum(name, dict([(k, k) for k in data]), type=str)

DocumentType = create_dynamic_enum('DocumentType', DOCUMENT_TYPES)

class DocumentMetadata(BaseModel):
    issue_date: str = Field(description="Date issued, format: YYYY-MM-DD.", example="2025-01-02")
    document_type: DocumentType = Field(description="Type of document.", example="fatura")
    issuing_party: str = Field(description="Issuer name, one word if possible.", example="Amazon")
    service_name: Optional[str] = Field(description="Product/service name if applicable.", example="Youtube Premium")
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
        image = pdf2image.convert_from_path(str(pdf_path), first_page=1, last_page=1)[0]
        img_buffer = io.BytesIO()
        image.save(img_buffer, format="jpeg")
        img_b64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")
    except Exception as e:
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
        return metadata
    except Exception as e:
        raise RuntimeError(f"Classification failed for: {pdf_path}") from e

# ------------------- RENAMING & PROCESSING -------------------

def rename_single_pdf(pdf_path: Path, file_hash: str, target_path: Path, known_hashes: set):
    try:
        metadata = classify_pdf_document(pdf_path, file_hash)
        filename = file_name_from_metadata(metadata, file_hash)
        new_pdf_path = target_path / filename

        shutil.copy2(pdf_path, new_pdf_path)

        with open(new_pdf_path.with_suffix('.json'), "w", encoding="utf-8") as f:
            json.dump(metadata.model_dump(), f, indent=4)

        known_hashes.add(file_hash)
        print(f"Processed: {pdf_path.name} → {filename}")
    except Exception as e:
        print(f"Failed to process {pdf_path.name}: {e}")

def rename_pdf_files(pdf_paths, file_hash_map, known_hashes, target_path, max_workers=4):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(tqdm(
            executor.map(
                lambda p: rename_single_pdf(p, file_hash_map[p], target_path, known_hashes),
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

# ------------------- MAIN -------------------

def process_folder(source_path: str, target_path: str, task: str):
    source_path = Path(source_path)
    target_path = Path("./output/")
    target_path.mkdir(parents=True, exist_ok=True)

    if task == "extract":
        print("Building hash index from metadata files...")
        known_hashes = build_output_hash_index(target_path).keys()

        print("Scanning for new PDFs...")
        pdf_paths = find_pdf_files(source_path)
        
        print(f"Calculating hashes for {len(pdf_paths)} PDFs...")
        file_hash_map = {pdf: hash_file(pdf) for pdf in tqdm(pdf_paths, desc="Hashing files")}
        files_to_process = [pdf for pdf in pdf_paths if file_hash_map[pdf] not in known_hashes]

        print(f"Found {len(files_to_process)} new PDFs.")
        rename_pdf_files(files_to_process, file_hash_map, known_hashes, target_path)
        print("Extraction complete.")

    elif task == "rename":
        print("Renaming existing PDF files and metadata based on metadata...")
        rename_existing_files(source_path)
        print("Renaming complete.")

    elif task == "validate":
        print("Validating existing metadata and PDFs...")
        _ = validate_metadata(source_path)
        print("Validation complete.")

    else:
        print("Invalid task specified. Use 'extract', 'rename', or 'validate'.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a folder of PDF files.")
    parser.add_argument("task", type=str, help="Specify task: 'extract', 'rename', or 'validate'.")
    parser.add_argument("source_path", type=str, help="Path to documents folder.")
    parser.add_argument("--target_path", type=str, default="./output/", help="Path to output folder.")
    args = parser.parse_args()
    process_folder(args.source_path, args.target_path, args.task)


