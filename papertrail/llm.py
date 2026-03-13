"""LLM prompts, tools, and classification utilities."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from papertrail.logging_utils import get_logger
from papertrail.models import DocumentMetadataRaw
from papertrail.qr.models import QRExtractedMetadata

logger = get_logger("llm")


def _extract_json_from_response(content: str) -> str:
    """Extract JSON from LLM response, handling markdown code blocks."""
    if "```json" in content:
        return content.split("```json")[1].split("```")[0].strip()
    if "```" in content:
        return content.split("```")[1].split("```")[0].strip()
    return content


def build_extraction_tools(exclude_fields: set[str] | None = None) -> list[dict]:
    """Build the structured extraction tool schema."""
    import copy

    schema = copy.deepcopy(DocumentMetadataRaw.model_json_schema())
    if exclude_fields:
        for field in exclude_fields:
            schema["properties"].pop(field, None)
        if "required" in schema:
            schema["required"] = [field for field in schema["required"] if field not in exclude_fields]

    return [
        {
            "type": "function",
            "function": {
                "name": "extract_document_metadata",
                "description": "Extract and classify document metadata. Return both raw text and normalized canonical forms.",
                "parameters": schema,
            },
        }
    ]


def get_qr_exclusions(qr_metadata: QRExtractedMetadata) -> tuple[set[str], dict[str, Any]]:
    """Return schema fields that QR extraction already supplied."""
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


def get_system_prompt_classify(
    known_document_types: list[str],
    known_issuing_parties: list[str],
    pre_extracted: dict[str, Any] | None = None,
    multi_qr_info: dict[str, Any] | None = None,
) -> str:
    """Build the single-call document classification prompt."""
    prompt = (
        f"You are an expert document classification assistant. "
        f"Today's date is {datetime.now().strftime('%Y-%m-%d')}. "
        "Given a document image, extract metadata and normalize it to canonical forms in a SINGLE step. "
        "Use all available visual, textual, and layout cues. "
        "Be strict about field formats (e.g., dates as YYYY-MM-DD, currency as ISO code). "
        "\n\n"
        "## Raw Fields (preserve exact text from the document)\n\n"
        "For issuing_party_raw, extract the EXACT text as it appears — do NOT normalize. "
        "Examples: 'Anthropic, PBC', 'Amazon Web Services, Inc.', 'Utility Provider Portugal - Comunicações Pessoais, S.A.'\n\n"
        "For document_type_raw, extract ONLY the core document type label as written, "
        "stripped of dates/periods/numbers. Preserve original language and case. "
        "Examples: 'Detalhe da Fatura de Abril 2024' -> 'Fatura', "
        "'Final Invoice for the August 2025 Billing Period' -> 'Invoice', "
        "'Nota de Crédito Abr 2022' -> 'Nota de Crédito'.\n\n"
        "## Normalized Fields (canonical slug-cased forms)\n\n"
        "For document_type, normalize the raw type to a canonical slug-cased value:\n"
        f"- Known types: {', '.join(t for t in known_document_types if t != '$UNKNOWN$')}\n"
        "- If a known type matches, use it EXACTLY (case-sensitive)\n"
        "- If no match, suggest a new slug-cased name (lowercase, hyphen-separated, English, "
        "e.g. tax-*, bank-*, invoice-*)\n"
        "- Only use '$UNKNOWN$' if the raw value is empty or truly unidentifiable\n\n"
        "For issuing_party, normalize the raw issuer to a canonical slug-cased value:\n"
        f"- Known parties: {', '.join(p for p in known_issuing_parties if p != '$UNKNOWN$')}\n"
        "- If a known party matches, use it EXACTLY (case-sensitive)\n"
        "- If no match, produce a clean short name: lowercase, strip legal suffixes "
        "(Inc., Ltd., S.A., Lda., PBC), use the most recognizable form "
        "(e.g., 'Anthropic, PBC' -> 'anthropic', 'Amazon Web Services' -> 'amazon')\n"
        "- Only use '$UNKNOWN$' if the raw value is empty or truly unidentifiable\n\n"
        "## Other Fields\n\n"
        "For document_title, extract the specific SUBJECT, PRODUCT, SERVICE, or TRANSACTION. "
        "This is NOT the document type — it distinguishes this document from others of the same type and issuer. "
        "Keep it concise (max ~60 characters). "
        "Examples: 'YouTube Premium', 'Claude API', 'Saúde Multicare Individual'. "
        "If no specific subject beyond what document_type already captures, leave null.\n\n"
        "For issuer_tax_number, look for tax identification numbers (NIF, VAT, EIN). "
        "Include country prefix when visible (e.g., DETESTOWNER). "
        "Omit prefix only for Portuguese documents where no prefix is shown. Null if not visible.\n\n"
        "For locale, use BCP-47 format based on language, currency, tax ID format "
        "(e.g., 'pt-PT', 'en-US', 'es-ES'). Null if uncertain.\n\n"
        "If a value cannot be extracted, use '$UNKNOWN$'. "
        "Do not guess or hallucinate values. "
        "For 'reasoning', briefly explain your choices. "
        "Prefer dates closest to today when uncertain."
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

    if multi_qr_info:
        issuers_str = ", ".join(multi_qr_info.get("issuers", []))
        prompt += (
            "\n\n"
            f"IMPORTANT: This PDF contains {multi_qr_info['count']} separate invoices "
            f"(detected by QR codes) from the following issuers: {issuers_str}. "
            "These individual invoices are already extracted separately as sub-documents. "
            "Classify the OVERALL document — the wrapper/aggregator/statement that contains these invoices. "
            "Focus on who issued this aggregator document and its overall type (e.g., extracto, statement, invoice-receipt). "
            "Do NOT classify it based on any individual embedded invoice or payment reference."
        )

    return prompt


def normalize_issuing_party(
    raw_name: str,
    client,
    model_id: str | None,
    known_issuing_parties: list[str],
) -> str:
    """Normalize a single issuing party name against the known canonical list."""
    if client is None:
        return "$UNKNOWN$"

    prompt = (
        "Normalize this company name to a canonical slug form.\n\n"
        f'Raw name: "{raw_name}"\n\n'
        f"Known parties: {', '.join(p for p in known_issuing_parties if p != '$UNKNOWN$')}\n\n"
        "If a known party matches, use it EXACTLY. Otherwise produce a clean short name: "
        "lowercase, strip legal suffixes (Inc., Ltd., S.A., Lda., PBC), use the most "
        "recognizable form.\n\n"
        'Respond in JSON: {"issuing_party": "normalized_name"}'
    )

    try:
        response = client.chat.completions.create(
            model=model_id,
            max_tokens=256,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if not content:
            return "$UNKNOWN$"
        content = _extract_json_from_response(content)
        result = json.loads(content)
        return result.get("issuing_party", "$UNKNOWN$")
    except Exception as exc:
        logger.error(f"NIF normalization failed: {exc}")
        return "$UNKNOWN$"


get_system_prompt_raw_extraction = get_system_prompt_classify
