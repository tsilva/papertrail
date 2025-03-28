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

class DocumentType(str, Enum):
    INVOICE = "fatura"
    INVOICE_RECEIPT = "fatura-recibo"
    RECEIPT = "recibo"
    CREDIT_NOTE = "nota credito"
    BANK_STATEMENT = "extrato bancario"
    BANK_TRANSFER = "transferencia bancaria"
    FOLHA_FERIAS = "folha ferias"
    CONTRATO_ADESAO = "contrato adesao"
    REFERENCIA_PAGAMENTO = "referencia pagamento"
    PEDIDO_ATUALIZACAO_DADOS = "pedido atualizacao dados"
    SEGURO_AUTOMOVEL = "seguro automovel"
    CUSTOS_ACCOES = "custos acoes"
    PEDIDO_INFORMACAO = "pedido informacao"
    DECLARACAO_CIRCULACAO = "declaracao circulacao"
    DECLARACAO_PERIODICA_IVA = "declaracao periodica iva"
    COMPROVATIVO_ENTREGA = "comprovativo entrega"
    NOTIFICACAO = "notificacao"
    NOTA_LANCAMENTO = "nota lancamento"
    IBAN = "iban"
    UNKNOWN = "unknown"

class DocumentMetadata(BaseModel):
    issue_date: str = Field(description="Date issued, format: YYYY-MM-DD.", example="2025-01-02")
    document_type: DocumentType = Field(description="Type of document.", example="fatura")
    issuing_party: str = Field(description="Issuer name, one word if possible.", example="Amazon")
    service_name: Optional[str] = Field(description="Product/service name if applicable.", example="Youtube Premium")
    total_amount: Optional[float] = Field(default=None, description="Total currency amount.")
    total_amount_currency: Optional[str] = Field(description="Currency of the total amount.", example="EUR")
    confidence: float = Field(description="Confidence score between 0 and 1.")
    reasoning: str = Field(description="Why this classification was chosen.")

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
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()[:8]

def extract_hash_from_filename(filename: str) -> Optional[str]:
    parts = filename[:-4].split(" - ")
    return parts[-1] if parts and len(parts[-1]) == 8 else None

def build_output_hash_index(output_path: Path) -> set:
    return {
        extract_hash_from_filename(file)
        for root, _, files in os.walk(output_path)
        for file in files if file.lower().endswith(".pdf")
        if extract_hash_from_filename(file)
    }

def sanitize_filename_component(s: str) -> str:
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[\\/*?:"<>|]', '', s).strip()
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

def classify_pdf_document(pdf_path: Path) -> DocumentMetadata:
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

        return DocumentMetadata.model_validate(tool_result)
    except Exception as e:
        raise RuntimeError(f"Classification failed for: {pdf_path}") from e

# ------------------- RENAMING & PROCESSING -------------------

def rename_single_pdf(pdf_path: Path, file_hash: str, target_path: Path, known_hashes: set):
    metadata = classify_pdf_document(pdf_path)
    filename = file_name_from_metadata(metadata, file_hash)
    new_pdf_path = target_path / filename

    shutil.copy2(pdf_path, new_pdf_path)

    with open(new_pdf_path.with_suffix('.json'), "w", encoding="utf-8") as f:
        json.dump(metadata.model_dump(), f, indent=4)

    known_hashes.add(file_hash)
    print(f"Processed: {pdf_path.name} → {filename}")

def rename_pdf_files(pdf_paths, file_hash_map, known_hashes, target_path, max_workers=4):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(tqdm(
            executor.map(
                lambda p: rename_single_pdf(p, file_hash_map[p], target_path, known_hashes),
                pdf_paths
            ),
            total=len(pdf_paths)
        ))

# ------------------- MAIN -------------------

def process_folder(source_path: str):
    source_path = Path(source_path)
    target_path = Path("./output/")
    target_path.mkdir(parents=True, exist_ok=True)

    print("Building hash index...")
    known_hashes = build_output_hash_index(target_path)

    print("Scanning for new PDFs...")
    pdf_paths = find_pdf_files(source_path)

    file_hash_map = {pdf: hash_file(pdf) for pdf in pdf_paths}
    files_to_process = [pdf for pdf in pdf_paths if file_hash_map[pdf] not in known_hashes]

    print(f"Found {len(files_to_process)} new PDFs.")
    rename_pdf_files(files_to_process, file_hash_map, known_hashes, target_path)
    print("All done.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process a folder of PDF files.")
    parser.add_argument("source_path", type=str, help="Path to PDF folder.")
    args = parser.parse_args()
    process_folder(args.source_path)