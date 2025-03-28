import io
import os
import json
import base64
import shutil
import anthropic
import pdf2image
from enum import Enum
from typing import Tuple
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

MODEL_ID = "claude-3-7-sonnet-latest"

class DocumentType(str, Enum):
    INVOICE = "fatura"
    INVOICE_RECEIPT = "fatura-recibo"
    RECEIPT = "recibo"
    CREDIT_NOTE = "nota de credito"
    BANK_STATEMENT = "extrato bancario"
    BANK_TRANSFER = "transferencia bancaria"
    OTHER = "outro"

def document_type_abbreviation(doc_type: DocumentType) -> str:
    abbreviations = {
        DocumentType.INVOICE: "FTR",
        DocumentType.INVOICE_RECEIPT: "FTRCB",
        DocumentType.RECEIPT: "RCT",
        DocumentType.CREDIT_NOTE: "NCR",
        DocumentType.OTHER: "OTH"
    }
    return abbreviations.get(doc_type, "OTH")

class DocumentMetadata(BaseModel):
    """
    Represents a high-level classification of a document.
    """
    issue_date: str = Field(
        description="Date the document was issued, if available. Format: YYYY-MM-DD.",
        example="2025-01-02"
    )
    document_type: DocumentType = Field(
        description="What kind of document this is, based on its visual layout and content."
    )
    issuing_party: str = Field(
        description="Name of the party that issued the document, if available. As a single word if possible.",
        example="Amazon"
    )
    description_slug: str = Field(
        description="A short, URL-friendly description of what the document contains.",
        example="combustivel"
    )
    total_amount: Optional[float] = Field(
        description="Total amount mentioned in the document, if applicable.",
        example=99.99
    )
    confidence: float = Field(
        description="Confidence in the classification, between 0 and 1."
    )
    reasoning: str = Field(
        description="Brief explanation of why this document was classified this way."
    )


TOOLS = [
    {
        "name": "classify_document_type",
        "description": """
Classify what kind of document this is based on layout, visible content, and formatting. Be accurate and explain your reasoning briefly.
""".strip(),
        "input_schema": DocumentMetadata.model_json_schema()
    }
]

def file_name_from_metadata(metadata: DocumentMetadata) -> str:
    document_type = metadata.document_type
    issue_date = metadata.issue_date
    issuing_party = metadata.issuing_party
    description_slug = metadata.description_slug
    file_name = f"{issue_date} - {document_type} - {issuing_party} - {description_slug}.pdf"
    file_name_l = file_name.lower()
    return file_name_l

def classify_pdf_document(pdf_path: Path) -> Tuple[DocumentType, str]:
    client = anthropic.Anthropic()

    # Convert first page of PDF to image
    first_page_image = pdf2image.convert_from_path(str(pdf_path), first_page=1, last_page=1)[0]

    # Convert to base64
    img_buffer = io.BytesIO()
    first_page_image.save(img_buffer, format="jpeg")
    img_b64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")

    # Send to Claude
    response = client.messages.create(
        model=MODEL_ID,
        max_tokens=4096,
        temperature=0,
        system=[
            {
                "type": "text",
                "text": """
You are a document classification assistant. Use layout, structure, and visible content to determine document type.
""".strip(),
                "cache_control": {"type": "ephemeral"}
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    { 
                        "type": "text", "text": """
What type of document is this? Use the structured tool."
""".strip()         
                    },
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": img_b64
                        }
                    }
                ]
            }
        ],
        tools=TOOLS
    )

    # Parse Claude tool output
    tool_result = None
    for content in response.content:
        if hasattr(content, "input"):
            tool_result = content.input
            break

    if not tool_result:
        raise ValueError("Claude did not return structured classification.")

    # Validate using pydantic
    try:
        metadata = DocumentMetadata.model_validate(tool_result)
        return metadata
    except Exception as e:
        raise ValueError(f"Invalid tool result: {tool_result}") from e
  
def find_pdf_files(folder_path):
    pdf_files = []
    
    # Walk through directory tree
    for root, _, files in os.walk(folder_path):
        # Check each file in current directory
        for file in files:
            # Check if file ends with .pdf (case insensitive)
            if file.lower().endswith('.pdf'):
                # Create full path by joining root and filename
                full_path = os.path.join(root, file)
                # Convert to absolute path
                full_path = os.path.abspath(full_path)
                full_path = Path(full_path)
                pdf_files.append(full_path)
    
    return pdf_files

def excluded_processed_pdf_files(pdf_paths):
    return [x for x in pdf_paths if not os.path.exists(x.with_suffix('.json'))]
   
def rename_single_pdf(pdf_path):
    metadata = classify_pdf_document(pdf_path)
    filename = file_name_from_metadata(metadata)
    new_path = pdf_path.parent / filename
    os.rename(pdf_path, new_path)

    json_path = new_path.with_suffix('.json')
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(metadata.model_dump(), f, indent=4)
        
    print(f"Renamed {pdf_path.name} to {new_path.name}")

def rename_pdf_files(pdf_paths, max_workers=4):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        list(tqdm(executor.map(rename_single_pdf, pdf_paths), total=len(pdf_paths)))

def process_folder(source_path: str):
    target_path = Path("./output/")
    if not os.path.exists(target_path):
        print("Copying files...")
        source_path = Path(source_path)
        target_path.mkdir(parents=True)
        shutil.copytree(source_path, target_path, dirs_exist_ok=True)
        print("Files copied.")

    print("Renaming files...")
    pdf_paths = find_pdf_files(target_path)
    unprocessed_paths = excluded_processed_pdf_files(pdf_paths)
    rename_pdf_files(unprocessed_paths)
    print("Files renamed.")

if __name__ == "__main__":
    process_folder("/mnt/c/Users/engti/Desktop/cronologia-20250328T174115Z-001/cronologia/")
