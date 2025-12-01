#!/usr/bin/env python3
"""Debug script to test classification on a specific PDF document."""

import os
import io
import json
import base64
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

import openai
import fitz
from PIL import Image, ImageEnhance
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load config
CONFIG_DIR = Path.home() / ".documentor"
ENV_PATH = CONFIG_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH, override=True)

OPENROUTER_MODEL_ID = os.getenv("OPENROUTER_MODEL_ID")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

print(f"Model: {OPENROUTER_MODEL_ID}")
print(f"Base URL: {OPENROUTER_BASE_URL}")

client = openai.OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)


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
    "If a value cannot be extracted, use '$UNKNOWN$' for that field. "
    "Do not guess or hallucinate values. "
    "For 'reasoning', briefly explain your choices and any uncertainties. "
    "This tool is most often used to classify recent documents. "
    "If you are unsure between multiple possible dates, prefer the one closest to today's date."
)


def render_pdf_to_images(pdf_path: Path) -> list[str]:
    """Render PDF pages to base64 encoded JPEG images."""
    doc = fitz.open(str(pdf_path))
    images_b64 = []
    num_pages = min(2, len(doc))
    
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
        print(f"Page {i+1}: {len(img_b64)} bytes base64")
    
    doc.close()
    return images_b64


def classify_document(pdf_path: Path):
    """Classify a PDF document and return full debug info."""
    print(f"\n{'='*60}")
    print(f"Classifying: {pdf_path}")
    print(f"{'='*60}")
    
    # Render images
    images_b64 = render_pdf_to_images(pdf_path)
    print(f"Rendered {len(images_b64)} page(s)")
    
    # Build user content
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
    
    print(f"\nSending request to OpenRouter...")
    print(f"System prompt length: {len(SYSTEM_PROMPT_RAW_EXTRACTION)} chars")
    print(f"Tool schema: {json.dumps(TOOLS_RAW_EXTRACTION[0]['function']['parameters'], indent=2)}")
    
    response = client.chat.completions.create(
        model=OPENROUTER_MODEL_ID,
        max_tokens=4096,
        temperature=0,
        messages=messages,
        tools=TOOLS_RAW_EXTRACTION,
        tool_choice={"type": "function", "function": {"name": "extract_document_metadata"}},
    )
    
    print(f"\n{'='*60}")
    print("FULL RESPONSE:")
    print(f"{'='*60}")
    print(f"ID: {response.id}")
    print(f"Model: {response.model}")
    print(f"Created: {response.created}")
    
    choice = response.choices[0]
    print(f"\nFinish reason: {choice.finish_reason}")
    print(f"Message role: {choice.message.role}")
    print(f"Message content: {choice.message.content}")
    print(f"Tool calls: {choice.message.tool_calls}")
    
    if choice.message.tool_calls:
        for i, tc in enumerate(choice.message.tool_calls):
            print(f"\n--- Tool Call {i} ---")
            print(f"ID: {tc.id}")
            print(f"Type: {tc.type}")
            print(f"Function name: {tc.function.name}")
            print(f"Function arguments: {tc.function.arguments}")
            
            # Parse and pretty print the arguments
            try:
                args = json.loads(tc.function.arguments)
                print(f"\nParsed arguments:")
                print(json.dumps(args, indent=2))
            except json.JSONDecodeError as e:
                print(f"Failed to parse arguments: {e}")
    else:
        print("\n⚠️ NO TOOL CALLS RETURNED!")
        print("The model did not call the extraction tool.")
        print("This is the error that caused the classification failure.")
    
    if response.usage:
        print(f"\nUsage:")
        print(f"  Prompt tokens: {response.usage.prompt_tokens}")
        print(f"  Completion tokens: {response.usage.completion_tokens}")
        print(f"  Total tokens: {response.usage.total_tokens}")
    
    return response


if __name__ == "__main__":
    if len(sys.argv) < 2:
        # Default to the problematic file
        pdf_path = Path("/mnt/d/Takeout/takeout-20251201T170724Z-3-001/Takeout/Mail/All mail Including Spam and Trash/503-202511_c51bb65c/503-Recibo 202511.pdf")
    else:
        pdf_path = Path(sys.argv[1])
    
    if not pdf_path.exists():
        print(f"ERROR: File not found: {pdf_path}")
        sys.exit(1)
    
    classify_document(pdf_path)

