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
import openai
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
        print(f"[OK] Copied example config files to {config_dir}: {', '.join(files_copied)}.\nEdit these files before rerunning.")
        sys.exit(0)
    # Always ensure .env exists (even if not in example files)
    if not env_path.exists():
        env_path.touch()
        print(f"[OK] Created .env at {env_path}. Edit this file before rerunning.")
        sys.exit(0)
    return config_dir, env_path

# Always load .env from ~/.documentor/.env, create if missing
CONFIG_DIR, ENV_PATH = ensure_home_config_and_env()

from dotenv import load_dotenv
load_dotenv(dotenv_path=ENV_PATH, override=True)

OPENROUTER_MODEL_ID = os.getenv("OPENROUTER_MODEL_ID")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

openai_client = openai.OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

# ------------------- ENUMS & MODELS -------------------

import importlib.resources

def load_document_types():
    """Load document types from processed files directory by scanning metadata .json files."""
    processed_files_dir = os.getenv("PROCESSED_FILES_DIR")

    # Fallback list if env var not set or directory doesn't exist
    fallback_types = [
        "$UNKNOWN$", "bill", "certificate", "contract", "declaration", "email", "extract",
        "invoice", "letter", "notification", "other", "receipt", "report", "statement", "ticket"
    ]

    if not processed_files_dir or not Path(processed_files_dir).exists():
        return fallback_types

    document_types_set = set()
    processed_path = Path(processed_files_dir)

    # Scan all .json files in the processed directory
    for json_file in processed_path.rglob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                document_type = data.get("document_type")
                if document_type:
                    # Handle both string and enum-like string formats
                    if isinstance(document_type, str):
                        if document_type.startswith("DocumentType."):
                            document_type = document_type.split(".", 1)[-1]
                        document_types_set.add(document_type)
        except Exception:
            # Silently skip files that can't be read or parsed
            continue

    # If no types found, use fallback
    if not document_types_set:
        document_types_set = set(fallback_types)

    # Always ensure "$UNKNOWN$" is in the list
    document_types_set.add("$UNKNOWN$")

    # Return sorted list for consistency
    return sorted(document_types_set)

def load_issuing_parties():
    """Load issuing parties from processed files directory by scanning metadata .json files."""
    processed_files_dir = os.getenv("PROCESSED_FILES_DIR")

    # Fallback list if env var not set or directory doesn't exist
    fallback_parties = [
        "$UNKNOWN$", "ActivoBank", "Allianz", "Amazon", "Anthropic", "Antonio Martins & Filhos",
        "Apple", "Armando", "Ascendi", "AT", "Auchan", "Banco BEST", "Banco Invest",
        "Bandicam", "BIG", "Bitwarden", "BlackRock", "BP", "BPI", "Caetano Formula",
        "Carrefour", "CEPSA", "Cleverbridge", "Codota", "Cohere", "Coinbase",
        "Consensus", "Continente", "CTT", "Dacia", "DEGIRO", "Digital River",
        "DigitalOcean", "DOKKER", "E.Leclerc", "EUROPA", "ExpressVPN", "FGCT",
        "Fidelidade", "Fluxe", "Fundo de Compensação do Trabalho", "Galp", "GESPOST",
        "GitHub", "GONCALTEAM", "Google", "Google Commerce Limited", "Government",
        "GRUPO", "HONG KONG USGREEN LIMITED", "INE", "Intermarché", "International",
        "IRN", "IRS", "iServices", "iShares", "justETF", "Justica",
        "La Maison", "Leroy", "LuLuComfort", "LusoAloja", "M2030",
        "MANUEL ALVES DIAS, LDA", "MB WAY", "Melo, Nadais & Associados", "Microsoft",
        "MillenniumBCP", "Mini Soninha", "Ministério das Finanças", "Mobatek",
        "MONTEPIO", "Multibanco", "Multicare", "MyCommerce", "MyFactoryHub", "NordVPN",
        "NOS", "Notario", "NTI", "OCC", "OpenAI", "OpenRouter", "OUYINEN", "Paddle",
        "Parallels", "PayPal", "PCDIGA", "Pinecone", "PLIMAT", "Pluxee", "PRIO",
        "PRISMXR", "Puzzle Message, Unipessoal Lda.", "Quindi", "Redunicre",
        "RegistoLEI", "Renault", "República Portuguesa", "RescueTime", "Restaurant",
        "Securitas", "Segurança Social", "SEGURANÇA SOCIAL", "Shenzhen", "Sierra",
        "Sodexo", "Solred", "SONAE", "SRS Acquiom", "Swappie", "Sweatcoin",
        "Tesouraria", "TIAGO", "Tilda", "Together.ai", "TopazLabs", "Universal",
        "Universo", "$UNKNOWN$", "Vanguard", "Via Verde", "VIDRIO PAIS PORTUGAL",
        "VITALOPE", "Vodafone", "WisdomTree", "Worten", "xAI"
    ]

    if not processed_files_dir or not Path(processed_files_dir).exists():
        return fallback_parties

    issuing_parties_set = set()
    processed_path = Path(processed_files_dir)

    # Scan all .json files in the processed directory
    for json_file in processed_path.rglob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                issuing_party = data.get("issuing_party")
                if issuing_party:
                    # Handle both string and enum-like string formats
                    if isinstance(issuing_party, str):
                        if issuing_party.startswith("IssuingParty."):
                            issuing_party = issuing_party.split(".", 1)[-1]
                        issuing_parties_set.add(issuing_party)
        except Exception:
            # Silently skip files that can't be read or parsed
            continue

    # If no parties found, use fallback
    if not issuing_parties_set:
        issuing_parties_set = set(fallback_parties)

    # Always ensure "$UNKNOWN$" is in the list
    issuing_parties_set.add("$UNKNOWN$")

    # Return sorted list for consistency
    return sorted(issuing_parties_set)

DOCUMENT_TYPES = load_document_types()

def create_dynamic_enum(name, data):
    return Enum(name, dict([(k, k) for k in data]), type=str)

DocumentType = create_dynamic_enum('DocumentType', DOCUMENT_TYPES)

ISSUING_PARTIES = load_issuing_parties()
IssuingParty = create_dynamic_enum('IssuingParty', ISSUING_PARTIES)

class DocumentMetadataRaw(BaseModel):
    """Raw extracted metadata without enum constraints - first phase extraction."""
    issue_date: str = Field(description="Date issued, format: YYYY-MM-DD.", example="2025-01-02")
    document_type: str = Field(description="Type of document (as seen on document).", example="Invoice")
    issuing_party: str = Field(description="Issuer name (exactly as it appears on document).", example="Anthropic, PBC")
    service_name: Optional[str] = Field(description="Product/service name if applicable (as short as possible).", example="Youtube Premium")
    total_amount: Optional[float] = Field(default=None, description="Total currency amount.")
    total_amount_currency: Optional[str] = Field(description="Currency of the total amount.", example="EUR")
    confidence: float = Field(description="Confidence score between 0 and 1.")
    reasoning: str = Field(description="Why this classification was chosen.")

class DocumentMetadataInput(BaseModel):
    issue_date: str = Field(description="Date issued, format: YYYY-MM-DD.", example="2025-01-02")
    document_type: DocumentType = Field(description="Type of document.", example="invoice")
    issuing_party: IssuingParty = Field(description="Issuer name, must be one of the predefined issuing parties.", example="Amazon")
    service_name: Optional[str] = Field(description="Product/service name if applicable (as short as possible).", example="Youtube Premium")
    total_amount: Optional[float] = Field(default=None, description="Total currency amount.")
    total_amount_currency: Optional[str] = Field(description="Currency of the total amount.", example="EUR")
    confidence: float = Field(description="Confidence score between 0 and 1.")
    reasoning: str = Field(description="Why this classification was chosen.")

class DocumentMetadata(DocumentMetadataInput):
    content_hash: str = Field(description="Content-based SHA256 hash (first 8 chars) - based on rendered PDF pages.", example="a1b2c3d4", alias="hash")
    file_hash: Optional[str] = Field(default=None, description="File-based SHA256 hash for quick filtering (first 8 chars).", example="b2c3d4e5", alias="_old_hash")
    create_date: Optional[str] = Field(default=None, description="Date this metadata was created, format: YYYY-MM-DD.", example="2024-06-01")
    update_date: Optional[str] = Field(default=None, description="Date this metadata was last updated, format: YYYY-MM-DD.", example="2024-06-01")
    # Raw extracted values before normalization (suffix _raw to indicate raw value)
    document_type_raw: Optional[str] = Field(default=None, description="Original document type as extracted from document.")
    issuing_party_raw: Optional[str] = Field(default=None, description="Original issuing party name as extracted from document.")

    class Config:
        populate_by_name = True  # Allow both field name and alias

    @field_validator('issue_date', mode='before')
    @classmethod
    def validate_issue_date(cls, value):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "$UNKNOWN$"
        return value

    @field_validator('issuing_party', mode='before')
    @classmethod
    def validate_issuing_party(cls, value):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "$UNKNOWN$"
        if isinstance(value, str):
            # Clean up enum-formatted strings
            if value.startswith("IssuingParty."):
                value = value.split(".", 1)[-1]
            # Check if value is in the valid enum values
            if value not in [party for party in ISSUING_PARTIES]:
                return "$UNKNOWN$"
        return value

    @field_validator('document_type', mode='before')
    @classmethod
    def validate_document_type(cls, value):
        if value is None or (isinstance(value, str) and value.strip() == ""):
            return "$UNKNOWN$"
        if isinstance(value, str):
            # Clean up enum-formatted strings
            if value.startswith("DocumentType."):
                value = value.split(".", 1)[-1]
            # Check if value is in the valid enum values
            if value not in [dt for dt in DOCUMENT_TYPES]:
                return "$UNKNOWN$"
        return value

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

# ------------------- LLM TOOL SETUP -------------------

TOOLS_RAW_EXTRACTION = [
    {
        "type": "function",
        "function": {
            "name": "extract_document_metadata",
            "description": "Extract metadata from a document exactly as it appears.",
            "parameters": DocumentMetadataRaw.model_json_schema(),
        },
    }
]

SYSTEM_PROMPT_RAW_EXTRACTION = (
    f"You are an expert document extraction assistant. "
    f"Today's date is {datetime.now().strftime('%Y-%m-%d')}. "
    "Given a document image, extract metadata fields EXACTLY as they appear on the document. "
    "Use all available visual, textual, and layout cues. "
    "Be strict about field formats (e.g., dates as YYYY-MM-DD, currency as ISO code). "
    "\n\n"
    "For issuing_party and document_type, extract the EXACT text as it appears - do NOT try to normalize or standardize it. "
    "Examples: 'Anthropic, PBC' not 'Anthropic', 'Invoice' not 'invoice', 'Amazon Web Services' not 'Amazon'. "
    "\n\n"
    "For your orientation, here are the typical canonical values we work with:\n"
    f"- Document types: {', '.join(DOCUMENT_TYPES[:20])}{'...' if len(DOCUMENT_TYPES) > 20 else ''}\n"
    f"- Issuing parties: {', '.join(ISSUING_PARTIES[:30])}{'...' if len(ISSUING_PARTIES) > 30 else ''}\n"
    "\n"
    "NOTE: These lists are just for orientation. Always extract the EXACT text as it appears on the document, "
    "even if it doesn't match these canonical values. The raw text will be normalized in a later step.\n"
    "\n"
    "If a value cannot be extracted, use '$UNKNOWN$' for that field. "
    "Do not guess or hallucinate values. "
    "For 'reasoning', briefly explain your choices and any uncertainties. "
    "This tool is most often used to classify recent documents. "
    "If you are unsure between multiple possible dates, prefer the one closest to today's date."
)

# ------------------- UTILS -------------------

def hash_file_fast(path: Path) -> str:
    """
    Fast file-based hash for quick duplicate detection.
    Uses raw file bytes - much faster than content-based hashing.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b''):
            h.update(chunk)
    return h.hexdigest()[:8]

def hash_file(path: Path) -> str:
    """
    Generate content-based hash by rendering PDF pages as images.
    This detects true content duplicates even if PDF metadata differs.

    Args:
        path: Path to the PDF file

    Returns:
        First 8 characters of SHA256 digest of rendered page content
    """
    try:
        doc = fitz.open(str(path))
        page_hashes = []

        # Use deterministic rendering settings for consistency
        # 150 DPI provides good quality while being reasonably fast
        zoom = 150 / 72  # 72 is the default DPI
        mat = fitz.Matrix(zoom, zoom)

        # Iterate through all pages and render each as an image
        for page_num in range(len(doc)):
            page = doc[page_num]

            try:
                # Render page as pixmap (image) with deterministic settings
                # alpha=False ensures no transparency channel for consistency
                pix = page.get_pixmap(matrix=mat, alpha=False, colorspace=fitz.csRGB)

                # Get the raw pixel data
                img_data = pix.samples

                # Hash the pixel data
                page_hash = hashlib.sha256(img_data).hexdigest()
                page_hashes.append(page_hash)

                # Explicitly clean up pixmap to avoid memory leaks
                pix = None

            except Exception as e:
                # Skip pages that fail to render but continue with others
                continue

        doc.close()

        # Create a digest of all page hashes combined (in order)
        if page_hashes:
            combined = "".join(page_hashes)
            content_digest = hashlib.sha256(combined.encode()).hexdigest()
            return content_digest[:8]
        else:
            # No pages could be rendered - fall back to file hash
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b''): h.update(chunk)
            return h.hexdigest()[:8]

    except Exception as e:
        # If content-based hashing fails entirely, fall back to file hash
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b''): h.update(chunk)
        return h.hexdigest()[:8]

def build_output_hash_index(output_path: Path) -> dict:
    """Build index of both content hashes and file hashes."""
    hash_index = {}
    for root, _, files in os.walk(output_path):
        for file in files:
            if not file.lower().endswith(".json"):
                continue
            with open(Path(root) / file, "r", encoding="utf-8") as f:
                metadata = json.load(f)
                # Index by content hash (primary)
                content_hash = metadata.get('content_hash') or metadata.get('hash')  # Support both old and new
                if content_hash:
                    hash_index[content_hash] = Path(root) / file.replace(".json", ".pdf")
                # Also index by file hash (for quick filtering)
                file_hash = metadata.get('file_hash') or metadata.get('_old_hash')  # Support both old and new
                if file_hash:
                    hash_index[file_hash] = Path(root) / file.replace(".json", ".pdf")
    return hash_index

def sanitize_filename_component(s: str) -> str:
    s = unicodedata.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii')
    s = re.sub(r'[\\/*?:"<>|()\[\],]', '', s).strip()
    return re.sub(r'\s+', ' ', s)

def file_name_from_metadata(metadata: DocumentMetadata, file_hash: str) -> str:
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

def find_pdf_files(folder_paths):
    """Return all PDF files within one or multiple folders."""
    if isinstance(folder_paths, (str, Path)):
        folder_paths = [folder_paths]

    pdfs = []
    for folder_path in folder_paths:
        folder_path = Path(folder_path)
        if not folder_path.exists():
            continue
        for root, _, files in os.walk(folder_path):
            for file in files:
                if (
                    file.lower().endswith('.pdf')
                    and not file.startswith('.')
                    and (Path(root) / file).stat().st_size > 0
                ):
                    pdfs.append(Path(root) / file)
    return pdfs

# ------------------- CLASSIFICATION -------------------

def normalize_metadata(raw_metadata: DocumentMetadataRaw) -> tuple[str, str]:
    """
    Phase 2: Use LLM to intelligently map raw extracted values to canonical enum values.
    Returns: (normalized_document_type, normalized_issuing_party)
    """
    client = openai_client

    normalization_prompt = f"""You are a metadata normalization assistant. Your job is to map extracted document values to their canonical forms.

Given:
- Raw document_type: "{raw_metadata.document_type}"
- Raw issuing_party: "{raw_metadata.issuing_party}"

Available canonical document types:
{', '.join(DOCUMENT_TYPES)}

Available canonical issuing parties:
{', '.join(ISSUING_PARTIES)}

Task:
1. Map the raw document_type to the MOST APPROPRIATE canonical document type from the list
2. Map the raw issuing_party to the MOST APPROPRIATE canonical issuing party from the list

Rules:
- If no good match exists, use "$UNKNOWN$"
- Be flexible with variations (e.g., "Anthropic, PBC" → "Anthropic", "Invoice" → "invoice")
- Consider common abbreviations and full names
- Preserve the EXACT canonical value (case-sensitive)

Respond in JSON format:
{{
    "document_type": "canonical_value",
    "issuing_party": "canonical_value",
    "reasoning": "Brief explanation of mappings"
}}
"""

    try:
        response = client.chat.completions.create(
            model=OPENROUTER_MODEL_ID,
            max_tokens=1024,
            temperature=0,
            messages=[{"role": "user", "content": normalization_prompt}]
        )

        content = response.choices[0].message.content

        # Debug output
        if not content:
            print(f"DEBUG: Empty response from normalization LLM")
            print(f"DEBUG: Full response: {response}")
            return "$UNKNOWN$", "$UNKNOWN$"

        # Try to extract JSON from the response
        # Handle cases where the response might be wrapped in markdown code blocks
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        result = json.loads(content)
        doc_type = result.get("document_type", "$UNKNOWN$")
        issuing_party = result.get("issuing_party", "$UNKNOWN$")

        # Validate that the returned values are actually in the canonical lists
        if doc_type not in DOCUMENT_TYPES:
            print(f"DEBUG: Normalized doc_type '{doc_type}' not in canonical list, using $UNKNOWN$")
            doc_type = "$UNKNOWN$"
        if issuing_party not in ISSUING_PARTIES:
            print(f"DEBUG: Normalized issuing_party '{issuing_party}' not in canonical list, using $UNKNOWN$")
            issuing_party = "$UNKNOWN$"

        return doc_type, issuing_party

    except Exception as e:
        print(f"Normalization failed: {e}, using $UNKNOWN$ for both fields")
        import traceback
        traceback.print_exc()
        return "$UNKNOWN$", "$UNKNOWN$"

def classify_pdf_document(pdf_path: Path, file_hash: str) -> DocumentMetadata:
    client = openai_client

    try:
        # Use PyMuPDF to render the first two pages as images (if available)
        doc = fitz.open(str(pdf_path))
        images_b64 = []
        num_pages = min(2, len(doc))
        from PIL import Image, ImageEnhance
        for i in range(num_pages):
            page = doc[i]
            pix = page.get_pixmap()
            img = Image.open(io.BytesIO(pix.tobytes("jpeg")))
            enhancer = ImageEnhance.Contrast(img)
            img_enhanced = enhancer.enhance(2.0)
            img_buffer = io.BytesIO()
            img_enhanced.save(img_buffer, format="JPEG")
            img_b64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")
            images_b64.append(img_b64)
        doc.close()
        # --- end contrast boost ---
    except Exception as e:
        print(e)
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
            {"role": "system", "content": SYSTEM_PROMPT_RAW_EXTRACTION},
            {"role": "user", "content": user_content},
        ]

        response = client.chat.completions.create(
            model=OPENROUTER_MODEL_ID,
            max_tokens=4096,
            temperature=0,
            messages=messages,
            tools=TOOLS_RAW_EXTRACTION,
        )

        tool_calls = response.choices[0].message.tool_calls
        if not tool_calls:
            raise ValueError("OpenRouter did not return structured classification.")

        args = tool_calls[0].function.arguments
        raw_metadata = DocumentMetadataRaw.model_validate_json(args)

        # PHASE 2: Normalization
        normalized_doc_type, normalized_issuing_party = normalize_metadata(raw_metadata)

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
            content_hash=file_hash,  # file_hash parameter is actually the content hash
            document_type_raw=raw_metadata.document_type,
            issuing_party_raw=raw_metadata.issuing_party,
        )

        now = datetime.now().strftime("%Y-%m-%d")
        metadata.create_date = now
        metadata.update_date = now
        return metadata
    except Exception as e:
        print(e)
        raise RuntimeError(f"Classification failed for: {pdf_path}") from e

# ------------------- RENAMING & PROCESSING -------------------

def rename_single_pdf(pdf_path: Path, content_hash: str, processed_path: Path, known_hashes: set):
    try:
        # Calculate fast file-based hash
        file_hash = hash_file_fast(pdf_path)

        metadata = classify_pdf_document(pdf_path, content_hash)

        # Add the file hash to metadata
        metadata.file_hash = file_hash

        filename = file_name_from_metadata(metadata, content_hash)
        new_pdf_path = processed_path / filename

        shutil.copy2(pdf_path, new_pdf_path)

        with open(new_pdf_path.with_suffix('.json'), "w", encoding="utf-8") as f:
            json.dump(metadata.model_dump(by_alias=True), f, indent=4)

        known_hashes.add(content_hash)
        known_hashes.add(file_hash)  # Also add file hash to prevent re-processing
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

            content_hash = metadata.content_hash
            if not content_hash:
                raise ValueError("Missing 'content_hash' in metadata.")

            pdf_path = metadata_path.with_suffix(".pdf")
            if not pdf_path.exists():
                raise FileNotFoundError(f"Missing PDF for metadata: {pdf_path.name}")

            actual_hash = hash_file(pdf_path)
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
    valid_entries = validate_metadata(output_path)

    for old_pdf_path, metadata in valid_entries:
        content_hash = metadata.content_hash
        new_filename = file_name_from_metadata(metadata, content_hash)
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

            # Ensure issuing_party is just the value, not Enum repr
            if isinstance(metadata_dict.get("issuing_party"), Enum):
                metadata_dict["issuing_party"] = metadata_dict["issuing_party"].value
            elif (
                isinstance(metadata_dict.get("issuing_party"), str)
                and metadata_dict["issuing_party"].startswith("IssuingParty.")
            ):
                metadata_dict["issuing_party"] = metadata_dict["issuing_party"].split(".", 1)[-1]

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
            "content_hash",
            "file_hash",
            "filename",
            "filename_length",
            "document_type",
            "document_type_raw",
            "issuing_party",
            "issuing_party_raw",
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
    result = subprocess.run(cmd, shell=True, text=True)
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

    raw_dirs = [p for p in RAW_FILES_DIR.split(';') if p]

    # Step 1 & 2 for each raw dir
    for rd in raw_dirs:
        run_step(
            f'mbox-extractor "{rd}"',
            "Step 1: Google Takeout mbox extraction"
        )
        run_step(
            f'archive-extractor "{rd}" --passwords "{zip_passwords_file_path}"',
            "Step 2: Google Takeout zip extraction"
        )

    # Step 3: documentor extract_new
    raw_dirs_arg = ";".join(raw_dirs)
    run_step(
        f'"{sys.executable}" "{__file__}" extract_new "{PROCESSED_FILES_DIR}" --raw_path "{raw_dirs_arg}"',
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

def _task__extract_new(processed_path, raw_paths):
    print("Building hash index from metadata files...")
    known_hashes = set(build_output_hash_index(processed_path).keys())

    print("Scanning for new PDFs...")
    pdf_paths = find_pdf_files(raw_paths)
    print(f"Found {len(pdf_paths)} PDFs in raw directories")

    # STAGE 1: Fast file-based hash to quickly filter likely duplicates
    print(f"Stage 1: Quick filtering using fast file hashes...")
    fast_hash_map = {pdf: hash_file_fast(pdf) for pdf in tqdm(pdf_paths, desc="Fast hashing")}
    potentially_new = [pdf for pdf in pdf_paths if fast_hash_map[pdf] not in known_hashes]

    already_processed = len(pdf_paths) - len(potentially_new)
    print(f"  → Skipped {already_processed} already-processed files")
    print(f"  → {len(potentially_new)} files need content-based hashing")

    if not potentially_new:
        print("No new PDFs to process.")
        return

    # STAGE 2: Slow content-based hash only for potentially new files
    print(f"Stage 2: Content-based hashing for {len(potentially_new)} new files...")
    content_hash_map = {}

    # Use sequential processing (safer and more reliable)
    # The fast hash already filtered most files, so this is manageable
    for pdf in tqdm(potentially_new, desc="Content hashing"):
        try:
            content_hash = hash_file(pdf)
            content_hash_map[pdf] = content_hash
        except Exception as e:
            print(f"\n  Error hashing {pdf.name}: {e}")

    # Final filter: only process files with truly new content hashes
    files_to_process = [pdf for pdf in potentially_new if content_hash_map.get(pdf) not in known_hashes]

    print(f"Found {len(files_to_process)} truly new PDFs to process.")

    if files_to_process:
        rename_pdf_files(files_to_process, content_hash_map, known_hashes, processed_path)

    print("Extraction complete.")

def _task__rename_files(processed_path):
    print("Renaming existing PDF files and metadata based on metadata...")

    # Custom validation that skips filename check (since we're about to rename them)
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

            # Skip hash recalculation - trust the metadata
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

        # Skip if the filename hasn't changed
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
    print("Validating existing metadata and PDFs...")
    valid_entries = []
    errors = []
    json_files = list(processed_path.rglob("*.json"))

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

            actual_hash = hash_file(pdf_path)
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

            # Ensure issuing_party is just the value, not Enum repr
            if isinstance(metadata_dict.get("issuing_party"), Enum):
                metadata_dict["issuing_party"] = metadata_dict["issuing_party"].value
            elif (
                isinstance(metadata_dict.get("issuing_party"), str)
                and metadata_dict["issuing_party"].startswith("IssuingParty.")
            ):
                metadata_dict["issuing_party"] = metadata_dict["issuing_party"].split(".", 1)[-1]

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
            "content_hash",
            "file_hash",
            "filename",
            "filename_length",
            "document_type",
            "document_type_raw",
            "issuing_party",
            "issuing_party_raw",
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

def process_folder(task: str, processed_path: str, raw_paths=None, excel_output_path: str = None, regex_pattern: str = None, copy_dest_folder: str = None, check_schema_path: str = None):
    if raw_paths is not None:
        raw_paths = [Path(p) for p in raw_paths]
    processed_path = Path(processed_path)
    processed_path.mkdir(parents=True, exist_ok=True)

    if task == "extract_new": _task__extract_new(processed_path, raw_paths)
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
    parser.add_argument("--raw_path", type=str, help="Path to documents folder(s). Use ';' to separate multiple paths (required for 'extract_new' task).")
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
        raw_paths=raw_paths if args.task == "extract_new" else None,
        excel_output_path=args.excel_output_path,
        regex_pattern=args.regex_pattern,
        copy_dest_folder=args.copy_dest_folder,
        check_schema_path=check_schema_path if args.task == "check_files_exist" else args.check_schema_path
    )

if __name__ == "__main__":
    main()