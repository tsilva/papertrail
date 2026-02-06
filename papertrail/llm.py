"""LLM prompts, tools, and classification utilities."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from papertrail.logging_utils import get_logger, DocumentLogger
from papertrail.models import DocumentMetadataRaw, DOCUMENT_TYPES, ISSUING_PARTIES
from papertrail.qr.models import QRExtractedMetadata
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
        from papertrail.config import get_current_profile
        profile = get_current_profile()
        if profile and profile.profile_dir:
            rejected_path = profile.profile_dir / "rejected_values.yaml"
        else:
            rejected_path = Path(__file__).parent.parent / "profiles" / "default" / "rejected_values.yaml"
        _rejected_manager = RejectedValuesManager(rejected_path)
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


def build_extraction_schema(exclude_fields: set[str] | None = None) -> dict:
    """Build JSON schema for LLM extraction, excluding specified fields.

    Args:
        exclude_fields: Set of field names to exclude from the schema.
                        These fields won't be extracted by the LLM.

    Returns:
        JSON schema dict with excluded fields removed.
    """
    import copy
    schema = copy.deepcopy(DocumentMetadataRaw.model_json_schema())

    if not exclude_fields:
        return schema

    # Remove excluded fields from properties
    for field in exclude_fields:
        schema["properties"].pop(field, None)

    # Remove from required list
    if "required" in schema:
        schema["required"] = [f for f in schema["required"] if f not in exclude_fields]

    return schema


def build_extraction_tools(exclude_fields: set[str] | None = None) -> list[dict]:
    """Build tools list with dynamic schema.

    Args:
        exclude_fields: Set of field names to exclude from the extraction schema.

    Returns:
        List of tool definitions for the LLM.
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "extract_document_metadata",
                "description": "Extract metadata from a document exactly as it appears.",
                "parameters": build_extraction_schema(exclude_fields),
            },
        }
    ]


def get_qr_exclusions(qr_metadata: QRExtractedMetadata) -> tuple[set[str], dict[str, Any]]:
    """Get fields to exclude and pre-extracted values from QR metadata.

    When QR extraction succeeds, the extracted fields should be excluded from
    the LLM schema (to reduce tokens) but provided as context (to help the LLM
    cross-reference other fields).

    Args:
        qr_metadata: Metadata extracted from QR code.

    Returns:
        Tuple of (set of field names to exclude, dict of field->value for prompt context)
    """
    exclude = set()
    pre_extracted: dict[str, Any] = {}

    mappings = [
        ("issue_date", qr_metadata.issue_date),
        ("document_type", qr_metadata.document_type),
        ("total_amount", qr_metadata.total_amount),
        ("total_amount_currency", qr_metadata.total_amount_currency),
        ("issuer_tax_number", qr_metadata.issuer_tax_number),
        ("locale", qr_metadata.locale),
    ]

    for field, value in mappings:
        if value is not None:
            exclude.add(field)
            pre_extracted[field] = value

    return exclude, pre_extracted


def get_system_prompt_raw_extraction(pre_extracted: dict[str, Any] | None = None) -> str:
    """
    Get the system prompt for raw metadata extraction.

    Includes the current date and sample enum values for context.
    When pre_extracted values are provided (from QR code extraction),
    they are included as context for the LLM but marked as already extracted.

    Args:
        pre_extracted: Dict of field names to values already extracted from QR code.
                       These fields are excluded from LLM schema but provided for context.

    Returns:
        System prompt string
    """
    prompt = (
        f"You are an expert document extraction assistant. "
        f"Today's date is {datetime.now().strftime('%Y-%m-%d')}. "
        "Given a document image, extract metadata fields EXACTLY as they appear on the document. "
        "Use all available visual, textual, and layout cues. "
        "Be strict about field formats (e.g., dates as YYYY-MM-DD, currency as ISO code). "
        "\n\n"
        "For issuing_party, extract the EXACT text as it appears - do NOT try to normalize or standardize it. "
        "Examples: 'Anthropic, PBC' not 'Anthropic', 'Amazon Web Services' not 'Amazon'. "
        "\n\n"
        "For document_title, extract the specific SUBJECT, PRODUCT, SERVICE, or TRANSACTION described in the document. "
        "This is NOT the document type - it distinguishes this document from others of the same type and issuer. "
        "Examples: 'YouTube Premium', 'Claude API', 'TRANSFERÊNCIA PONTUAL A DÉBITO', 'Saúde Multicare Individual'. "
        "If no specific subject beyond what document_type already captures, leave null. "
        "\n\n"
        "For document_type, extract ONLY the core document type label. "
        "Do NOT include dates, billing periods, months, years, reference numbers, or other contextual information. "
        "Examples: 'Detalhe da Fatura de Abril 2024' → extract 'Fatura', "
        "'Final Invoice for the August 2025 Billing Period' → extract 'Invoice', "
        "'Nota de Crédito Abr 2022' → extract 'Nota de Crédito'. "
        "Extract the type label exactly as written on the document (preserve case, language), just strip temporal context. "
        "\n\n"
        "For issuer_tax_number, look for tax identification numbers like:\n"
        "- Portuguese NIF (e.g., PTISSUER-TAX-ID or ISSUER-TAX-ID for Portuguese documents)\n"
        "- EU VAT numbers (e.g., DETESTOWNER, FRTESTOWNER01, IE1234567X)\n"
        "- US EIN (e.g., 12-3456789)\n"
        "ALWAYS include the country prefix (PT, DE, FR, IE, etc.) when visible on the document. "
        "Only omit prefix for Portuguese documents where no prefix is shown. If no tax ID is visible, leave it null.\n"
        "\n"
        "For locale, determine the document's country/language from:\n"
        "- Document text language (Portuguese, English, Spanish, etc.)\n"
        "- Currency (EUR often indicates European country)\n"
        "- Tax ID format (Portuguese NIF, Spanish CIF, US EIN)\n"
        "- Date format patterns and regional conventions\n"
        "Use BCP-47 format: 'pt-PT' for Portugal, 'en-US' for US, 'es-ES' for Spain.\n"
        "If uncertain, leave locale as null.\n"
        "\n"
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

    if pre_extracted:
        prompt += (
            "\n\n"
            "IMPORTANT: The following fields have already been extracted from a machine-readable "
            "source (QR code) with 100% accuracy. They are NOT in your schema - do NOT extract them. "
            "They are provided for context only:\n"
        )
        for field, value in pre_extracted.items():
            prompt += f"- {field}: {value}\n"

    return prompt


def normalize_metadata(
    raw_metadata: DocumentMetadataRaw,
    client,
    model_id: Optional[str] = None,
    mappings: Optional["MappingsManager"] = None,
    doc_logger: Optional[DocumentLogger] = None,
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

        # Validate Tier 1 results against current enums (stale mappings fall through to Tier 2)
        if doc_type and doc_type not in DOCUMENT_TYPES:
            logger.warning(f"[STALE-MAPPING] document_type: '{doc_type}' not in current enum for raw '{raw_metadata.document_type}', will re-normalize")
            if doc_logger:
                doc_logger.log_stale_mapping("document_type", raw_metadata.document_type, doc_type)
            doc_type = None
        if issuing_party and issuing_party not in ISSUING_PARTIES:
            logger.warning(f"[STALE-MAPPING] issuing_party: '{issuing_party}' not in current enum for raw '{raw_metadata.issuing_party}', will re-normalize")
            if doc_logger:
                doc_logger.log_stale_mapping("issuing_party", raw_metadata.issuing_party, issuing_party)
            issuing_party = None

        if doc_type and doc_logger:
            doc_logger.log_normalization("document_type", raw_metadata.document_type, doc_type, tier=1)
        if issuing_party and doc_logger:
            doc_logger.log_normalization("issuing_party", raw_metadata.issuing_party, issuing_party, tier=1)

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

        # Log LLM usage
        if doc_logger and response.usage:
            doc_logger.log_llm_usage(
                model_id,
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
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
            if doc_logger:
                doc_logger.log_rejected("document_type", raw_metadata.document_type, llm_doc_type)
            llm_doc_type = "$UNKNOWN$"
        if llm_issuing_party not in ISSUING_PARTIES:
            logger.warning(f"Rejected issuing_party '{llm_issuing_party}' (not in canonical list)")
            _log_rejected_value("issuing_parties", llm_issuing_party, raw_metadata.issuing_party)
            if doc_logger:
                doc_logger.log_rejected("issuing_party", raw_metadata.issuing_party, llm_issuing_party)
            llm_issuing_party = "$UNKNOWN$"

        # Use LLM results for fields that needed normalization
        if need_doc_type:
            doc_type = llm_doc_type
            if doc_logger:
                doc_logger.log_normalization("document_type", raw_metadata.document_type, doc_type, tier=2)
        if need_issuing_party:
            issuing_party = llm_issuing_party
            if doc_logger:
                doc_logger.log_normalization("issuing_party", raw_metadata.issuing_party, issuing_party, tier=2)

        # Save LLM mappings for reuse (including $UNKNOWN$ — so rejected values
        # are cached in TIER 1 and visible in mappings.yaml for user review)
        if mappings:
            if need_doc_type and raw_metadata.document_type != "$UNKNOWN$":
                mappings.add_mapping(
                    raw_metadata.document_type, doc_type, "document_types"
                )
                if doc_logger:
                    doc_logger.log_mapping_saved("document_types", raw_metadata.document_type, doc_type)
            if need_issuing_party and raw_metadata.issuing_party != "$UNKNOWN$":
                mappings.add_mapping(
                    raw_metadata.issuing_party, issuing_party, "issuing_parties"
                )
                if doc_logger:
                    doc_logger.log_mapping_saved("issuing_parties", raw_metadata.issuing_party, issuing_party)

        return doc_type, issuing_party

    except Exception as e:
        logger.error(f"Normalization failed: {e}, using $UNKNOWN$ for both fields")
        logger.debug("Traceback:", exc_info=True)
        return doc_type or "$UNKNOWN$", issuing_party or "$UNKNOWN$"
