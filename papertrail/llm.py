"""LLM prompts, tools, and classification utilities."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from papertrail.logging_utils import get_logger
from papertrail.models import DocumentMetadataRaw, DOCUMENT_TYPES, ISSUING_PARTIES
from papertrail.rejected import RejectedValuesManager

logger = get_logger('llm')

if TYPE_CHECKING:
    from papertrail.mappings import MappingsManager

# Global rejected values manager (lazy-loaded)
_rejected_manager: Optional[RejectedValuesManager] = None


def _get_rejected_manager() -> RejectedValuesManager:
    """Get or create the rejected values manager."""
    global _rejected_manager
    if _rejected_manager is None:
        config_dir = Path(__file__).parent.parent / "config"
        _rejected_manager = RejectedValuesManager(config_dir / "rejected_values.yaml")
    return _rejected_manager


def _log_rejected_value(field: str, normalized: str, raw: str) -> None:
    """Log a rejected normalization for review.

    Called when the LLM suggests a canonical value that's not in the allowed list.

    Args:
        field: Field name ('document_types' or 'issuing_parties')
        normalized: The canonical value suggested by the LLM (rejected)
        raw: The original raw value from extraction
    """
    manager = _get_rejected_manager()
    is_new = manager.add_rejected(field, normalized, raw)
    if is_new:
        logger.info(f"New rejected {field} logged: '{normalized}' (raw: '{raw}')")
    else:
        logger.debug(f"Duplicate rejection: '{normalized}' (raw: '{raw}')")


def _extract_json_from_response(content: str) -> str:
    """Extract JSON from LLM response, handling markdown code blocks."""
    if "```json" in content:
        return content.split("```json")[1].split("```")[0].strip()
    if "```" in content:
        return content.split("```")[1].split("```")[0].strip()
    return content


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
    model_id: Optional[str] = None,
    mappings: Optional["MappingsManager"] = None
) -> tuple[str, str]:
    """
    Phase 2: Normalize raw extracted values to canonical enum values.

    Uses a two-tier approach:
    1. TIER 1: Check persistent mappings file (instant, no LLM call)
    2. TIER 2: Fall back to LLM normalization, then save mapping for reuse

    Args:
        raw_metadata: Raw metadata from phase 1 extraction
        client: OpenAI client instance
        model_id: Model ID to use (defaults to OPENROUTER_MODEL_ID env var)
        mappings: Optional MappingsManager for persistent mapping lookup/storage

    Returns:
        Tuple of (normalized_document_type, normalized_issuing_party)
    """
    if model_id is None:
        model_id = os.getenv("OPENROUTER_MODEL_ID")

    doc_type = None
    issuing_party = None

    # TIER 1: Check mappings file first (no LLM call needed)
    if mappings:
        doc_type = mappings.get_mapping(raw_metadata.document_type, "document_types")
        issuing_party = mappings.get_mapping(raw_metadata.issuing_party, "issuing_parties")

        if doc_type and issuing_party:
            # Both found in mappings - no LLM needed!
            return doc_type, issuing_party

    # TIER 2: Fall back to LLM for unknown values
    # Determine which fields need LLM normalization
    need_doc_type = doc_type is None
    need_issuing_party = issuing_party is None

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
            logger.debug("Empty response from normalization LLM")
            logger.debug(f"Full response: {response}")
            return doc_type or "$UNKNOWN$", issuing_party or "$UNKNOWN$"

        # Extract JSON from the response (handle markdown code blocks)
        content = _extract_json_from_response(content)
        result = json.loads(content)
        llm_doc_type = result.get("document_type", "$UNKNOWN$")
        llm_issuing_party = result.get("issuing_party", "$UNKNOWN$")

        # Validate that the returned values are actually in the canonical lists
        if llm_doc_type not in DOCUMENT_TYPES:
            logger.warning(f"Rejected doc_type '{llm_doc_type}' (not in canonical list)")
            _log_rejected_value("document_types", llm_doc_type, raw_metadata.document_type)
            llm_doc_type = "$UNKNOWN$"
        if llm_issuing_party not in ISSUING_PARTIES:
            logger.warning(f"Rejected issuing_party '{llm_issuing_party}' (not in canonical list)")
            _log_rejected_value("issuing_parties", llm_issuing_party, raw_metadata.issuing_party)
            llm_issuing_party = "$UNKNOWN$"

        # Use LLM results for fields that needed normalization
        if need_doc_type:
            doc_type = llm_doc_type
        if need_issuing_party:
            issuing_party = llm_issuing_party

        # Save successful LLM mappings for reuse (as 'auto' pending review)
        # IMPORTANT: Don't save mappings that result in $UNKNOWN$ - these are rejections, not valid mappings
        if mappings:
            if need_doc_type and raw_metadata.document_type != "$UNKNOWN$" and doc_type != "$UNKNOWN$":
                mappings.add_mapping(
                    raw_metadata.document_type, doc_type, "document_types", confirmed=False
                )
            if need_issuing_party and raw_metadata.issuing_party != "$UNKNOWN$" and issuing_party != "$UNKNOWN$":
                mappings.add_mapping(
                    raw_metadata.issuing_party, issuing_party, "issuing_parties", confirmed=False
                )

        return doc_type, issuing_party

    except Exception as e:
        logger.error(f"Normalization failed: {e}, using $UNKNOWN$ for both fields")
        logger.debug("Traceback:", exc_info=True)
        return doc_type or "$UNKNOWN$", issuing_party or "$UNKNOWN$"
