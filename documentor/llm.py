"""LLM prompts, tools, and classification utilities."""

import json
import os
from datetime import datetime
from typing import Optional

from documentor.models import DocumentMetadataRaw, DOCUMENT_TYPES, ISSUING_PARTIES


def get_tools_raw_extraction() -> list[dict]:
    """
    Get the tool definition for raw metadata extraction.

    Returns:
        List containing the extraction tool definition
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "extract_document_metadata",
                "description": "Extract metadata from a document exactly as it appears.",
                "parameters": DocumentMetadataRaw.model_json_schema(),
            },
        }
    ]


# Static alias for backwards compatibility
TOOLS_RAW_EXTRACTION = get_tools_raw_extraction()


def get_system_prompt_raw_extraction() -> str:
    """
    Get the system prompt for raw metadata extraction.

    Includes the current date and sample enum values for context.

    Returns:
        System prompt string
    """
    return (
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


def normalize_metadata(
    raw_metadata: DocumentMetadataRaw,
    client,
    model_id: Optional[str] = None
) -> tuple[str, str]:
    """
    Phase 2: Use LLM to intelligently map raw extracted values to canonical enum values.

    Args:
        raw_metadata: Raw metadata from phase 1 extraction
        client: OpenAI client instance
        model_id: Model ID to use (defaults to OPENROUTER_MODEL_ID env var)

    Returns:
        Tuple of (normalized_document_type, normalized_issuing_party)
    """
    if model_id is None:
        model_id = os.getenv("OPENROUTER_MODEL_ID")

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
- Be flexible with variations (e.g., "Anthropic, PBC" -> "Anthropic", "Invoice" -> "invoice")
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
            model=model_id,
            max_tokens=1024,
            temperature=0,
            messages=[{"role": "user", "content": normalization_prompt}]
        )

        content = response.choices[0].message.content

        if not content:
            print(f"DEBUG: Empty response from normalization LLM")
            print(f"DEBUG: Full response: {response}")
            return "$UNKNOWN$", "$UNKNOWN$"

        # Extract JSON from the response (handle markdown code blocks)
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
