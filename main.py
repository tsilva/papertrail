import io
import os
import json
import base64
import shutil
import hashlib
import anthropic
import pdf2image
from enum import Enum
from typing import Tuple, Optional
from pathlib import Path
from pydantic import BaseModel, Field
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

MODEL_ID = "claude-3-7-sonnet-latest"

# ------------------- ENUMS & MODELS -------------------

class DocumentType(str, Enum):
    INVOICE = "fatura"
    INVOICE_RECEIPT = "fatura-recibo"
    RECEIPT = "recibo"
    CREDIT_NOTE = "nota de credito"
    BANK_STATEMENT = "extrato bancario"
    BANK_TRANSFER = "transferencia bancaria"
    FOLHA_FERIAS = "folha de ferias"
    CONTRATO_ADESAO = "contrato de adesao"
    REFERENCIA_PAGAMENTO = "referencia de pagamento"
    PEDIDO_ATUALIZACAO_DADOS = "pedido de atualizacao de dados"
    SEGURO_AUTOMOVEL = "seguro automovel"
    CUSTOS_ACCOES = "custos acoes"
    OTHER = "outro"

class DocumentDescription(str, Enum):
    UNKNOWN = "UNKNOWN"
    FATURA_CONTABILIDADE = "fatura contabilidade"
    GOOGLE_ONE = "google one"
    YOUTUBE_PREMIUM = "youtube premium"
    RESCUETIME = "rescuetime"
    PARALLELS = "parallels"
    PORTAGENS = "portagens"

class DocumentMetadata(BaseModel):
    issue_date: str = Field(description="Date issued, format: YYYY-MM-DD.", example="2025-01-02")
    document_type: DocumentType = Field(description="Type of document based on content.")
    document_description: DocumentDescription = Field(description="Description of document based on content.")
    issuing_party: str = Field(description="Issuer name, one word if possible.", example="Amazon")
    description_slug: str = Field(description="Short, URL-friendly doc description.", example="combustivel")
    total_amount: Optional[float] = Field(description="Total amount mentioned, if any.", example=99.99)
    confidence: float = Field(description="Confidence score between 0 and 1.")
    reasoning: str = Field(description="Why this classification was chosen.")

# ------------------- CLAUDE TOOL SETUP -------------------

TOOLS = [
    {
        "name": "classify_document_type",
        "description": "Classify the document based on layout and content, with reasoning.",
        "input_schema": DocumentMetadata.model_json_schema()
    }
]

# ------------------- UTILS -------------------

def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()[:8]

def extract_hash_from_filename(filename: str) -> Optional[str]:
    parts = filename[:-4].split(" - ")  # strip ".pdf" and split
    if parts and len(parts[-1]) == 8:
        return parts[-1]
    return None

def build_output_hash_index(output_path: Path) -> set:
    known_hashes = set()
    for root, _, files in os.walk(output_path):
        for file in files:
            if file.lower().endswith(".pdf"):
                h = extract_hash_from_filename(file)
                if h:
                    known_hashes.add(h)
    return known_hashes

def file_name_from_metadata(metadata: DocumentMetadata, file_hash: str) -> str:
    file_name = f"{metadata.issue_date} - {metadata.document_type} - {metadata.issuing_party} - {metadata.document_description} - {file_hash}.pdf"
    return file_name.lower()

def find_pdf_files(folder_path: Path):
    pdf_files = []
    for root, _, files in os.walk(folder_path):
        for file in files:
            if file.lower().endswith('.pdf'):
                pdf_files.append(Path(root) / file)
    return pdf_files

# ------------------- CLASSIFICATION -------------------

def classify_pdf_document(pdf_path: Path) -> DocumentMetadata:
    client = anthropic.Anthropic()

    first_page_image = pdf2image.convert_from_path(str(pdf_path), first_page=1, last_page=1)[0]
    img_buffer = io.BytesIO()
    first_page_image.save(img_buffer, format="jpeg")
    img_b64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")

    response = client.messages.create(
        model=MODEL_ID,
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

    tool_result = None
    for content in response.content:
        if hasattr(content, "input"):
            tool_result = content.input
            break

    if not tool_result:
        raise ValueError("Claude did not return structured classification.")

    try:
        metadata = DocumentMetadata.model_validate(tool_result)
        return metadata
    except Exception as e:
        raise ValueError(f"Invalid classification result: {tool_result}") from e

# ------------------- RENAMING & PROCESSING -------------------

def rename_single_pdf(pdf_path: Path, file_hash: str, target_path: Path):
    metadata = classify_pdf_document(pdf_path)
    filename = file_name_from_metadata(metadata, file_hash)
    new_pdf_path = target_path / filename

    shutil.copy2(pdf_path, new_pdf_path)

    json_path = new_pdf_path.with_suffix('.json')
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata.model_dump(), f, indent=4)

    print(f"Processed and copied: {pdf_path.name} -> {filename}")

def rename_pdf_files(pdf_paths, file_hash_map, known_hashes, target_path, max_workers=4):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(tqdm(
            executor.map(
                lambda p: rename_single_pdf(p, file_hash_map[p], target_path),
                pdf_paths
            ),
            total=len(pdf_paths)
        ))

# ------------------- MAIN ENTRY -------------------

def process_folder(source_path: str):
    source_path = Path(source_path)
    target_path = Path("./output/")
    target_path.mkdir(parents=True, exist_ok=True)

    print("Building hash index from output folder...")
    known_hashes = build_output_hash_index(target_path)

    print("Scanning for new PDF files...")
    pdf_paths = find_pdf_files(source_path)

    files_to_process = []
    file_hash_map = {}
    for pdf in pdf_paths:
        h = hash_file(pdf)
        if h not in known_hashes:
            files_to_process.append(pdf)
            file_hash_map[pdf] = h

    print(f"{len(files_to_process)} new files to process.")
    rename_pdf_files(files_to_process, file_hash_map, known_hashes, target_path)
    print("Processing complete.")

# ------------------- RUN -------------------

if __name__ == "__main__":
    process_folder("/mnt/c/Users/engti/Desktop/cronologia-20250328T174115Z-001/cronologia/")
