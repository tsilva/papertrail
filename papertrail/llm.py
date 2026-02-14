"""LLM prompts, tools, and classification utilities."""

import json
from datetime import datetime
from typing import Any, Optional

from papertrail.logging_utils import get_logger, DocumentLogger
from papertrail.models import DocumentMetadataRaw
from papertrail.enums import get_document_types, get_issuing_parties
from papertrail.qr.models import QRExtractedMetadata

logger = get_logger('llm')


def _extract_json_from_response(content: str) -> str:
    """Extract JSON from LLM response, handling markdown code blocks."""
    if "```json" in content:
        return content.split("```json")[1].split("```")[0].strip()
    if "```" in content:
        return content.split("```")[1].split("```")[0].strip()
    return content


def build_extraction_tools(exclude_fields: set[str] | None = None) -> list[dict]:
    """Build LLM tools list with extraction schema, optionally excluding fields."""
    import copy
    schema = copy.deepcopy(DocumentMetadataRaw.model_json_schema())

    if exclude_fields:
        for field in exclude_fields:
            schema["properties"].pop(field, None)
        if "required" in schema:
            schema["required"] = [f for f in schema["required"] if f not in exclude_fields]

    return [
        {
            "type": "function",
            "function": {
                "name": "extract_document_metadata",
                "description": "Extract metadata from a document exactly as it appears.",
                "parameters": schema,
            },
        }
    ]


def get_qr_exclusions(qr_metadata: QRExtractedMetadata) -> tuple[set[str], dict[str, Any]]:
    """Get (fields_to_exclude, pre_extracted_values) from QR metadata for LLM."""
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


def get_system_prompt_raw_extraction(pre_extracted: dict[str, Any] | None = None,
                                     multi_qr_info: dict[str, Any] | None = None) -> str:
    """Build system prompt for raw metadata extraction."""
    # Use live lists (includes session-confirmed values)
    doc_types = get_document_types()

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
        "Keep it concise (a few words, max ~60 characters). "
        "For multi-item invoices, use a brief category like 'computer hardware' or 'office supplies' instead of listing every product. "
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
        f"- Document types: {', '.join(doc_types[:20])}{'...' if len(doc_types) > 20 else ''}\n"
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


def normalize_metadata(
    raw_metadata: DocumentMetadataRaw,
    client,
    model_id: Optional[str] = None,
    doc_logger: Optional[DocumentLogger] = None,
) -> tuple[str, str]:
    """Normalize raw values to canonical forms via LLM. Returns (doc_type, issuing_party).

    When no good match exists in the known lists, the LLM suggests a new slug-cased
    name instead of falling back to $UNKNOWN$. The caller is responsible for confirming
    new values interactively.
    """
    # Use live lists (includes session-confirmed values)
    doc_types = get_document_types()
    issuing_parties = get_issuing_parties()

    normalization_prompt = f"""You are a metadata normalization assistant. Your job is to map extracted document values to their canonical forms.

Given:
- Raw document_type: "{raw_metadata.document_type}"
- Raw issuing_party: "{raw_metadata.issuing_party}"

Known document types:
{', '.join(doc_types)}

Known issuing parties:
{', '.join(p for p in issuing_parties if p != '$UNKNOWN$')}

Task:
1. Map the raw document_type to the MOST APPROPRIATE known document type from the list above
2. Map the raw issuing_party to the MOST APPROPRIATE known issuing party from the list above, or normalize to a clean canonical form

Rules for document_type:
- If a good match exists in the known list, use the EXACT canonical value (case-sensitive)
- If no good match exists, suggest the best slug-cased name following the naming convention (lowercase, hyphen-separated, English, namespace-prefixed where appropriate e.g. tax-*, bank-*, invoice-*)
- Only use "$UNKNOWN$" if the raw value is empty or truly unidentifiable

Rules for issuing_party:
- If a good match exists in the known list, use the EXACT canonical value (case-sensitive)
- If no good match exists, produce a clean, short company/entity name (e.g., "Anthropic, PBC" -> "anthropic", "Amazon Web Services, Inc." -> "amazon")
- Lowercase, strip legal suffixes (Inc., Ltd., S.A., Lda., PBC, etc.)
- Use the most recognizable short form of the name
- Only use "$UNKNOWN$" if the raw value is empty or truly unidentifiable

Respond in JSON format:
{{
    "document_type": "canonical_value",
    "issuing_party": "normalized_name",
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
            return "$UNKNOWN$", "$UNKNOWN$"

        content = _extract_json_from_response(content)
        result = json.loads(content)
        doc_type = result.get("document_type", "$UNKNOWN$")
        issuing_party = result.get("issuing_party", "$UNKNOWN$")

        # Log the normalization result (no rejection — new values are confirmed downstream)
        if doc_type not in doc_types:
            logger.debug(f"LLM suggested new doc_type '{doc_type}' (not in known list)")
        if issuing_party not in issuing_parties and issuing_party != "$UNKNOWN$":
            logger.debug(f"LLM suggested new issuing_party '{issuing_party}' (not in known list)")

        if doc_logger:
            doc_logger.log_normalization("document_type", raw_metadata.document_type, doc_type)
            doc_logger.log_normalization("issuing_party", raw_metadata.issuing_party, issuing_party)

        return doc_type, issuing_party

    except Exception as e:
        logger.error(f"Normalization failed: {e}, using $UNKNOWN$ for both fields")
        logger.debug("Traceback:", exc_info=True)
        return "$UNKNOWN$", "$UNKNOWN$"
