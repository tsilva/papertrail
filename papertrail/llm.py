"""LLM prompts, tools, and classification utilities."""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from typing import Any

from papertrail.logging_utils import get_logger
from papertrail.models import DocumentMetadataRaw
from papertrail.qr.models import QRExtractedMetadata

logger = get_logger("llm")

_LEGAL_SUFFIXES = {
    "inc",
    "incorporated",
    "ltd",
    "limited",
    "llc",
    "llp",
    "plc",
    "corp",
    "corporation",
    "company",
    "co",
    "sa",
    "lda",
    "pbc",
}


def _extract_json_from_response(content: str) -> str:
    """Extract JSON from LLM response, handling markdown code blocks."""
    if "```json" in content:
        return content.split("```json")[1].split("```")[0].strip()
    if "```" in content:
        return content.split("```")[1].split("```")[0].strip()
    return content


def _ascii_fold(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def _simplify_company_name(value: str) -> str:
    normalized = _ascii_fold(value).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    tokens = [token for token in normalized.split() if token]
    while tokens and tokens[-1] in _LEGAL_SUFFIXES:
        tokens.pop()
    while len(tokens) >= 2 and " ".join(tokens[-2:]) == "s a":
        tokens = tokens[:-2]
    return " ".join(tokens)


def _slugify_company_name(value: str) -> str:
    simplified = _simplify_company_name(value)
    if not simplified:
        return "$UNKNOWN$"
    return re.sub(r"\s+", "-", simplified).strip("-") or "$UNKNOWN$"


def _canonical_known_party(value: str, known_issuing_parties: list[str]) -> str | None:
    matches = {known.lower(): known for known in known_issuing_parties if known != "$UNKNOWN$"}
    return matches.get(value.lower())


def _heuristic_normalize_issuing_party(raw_name: str, known_issuing_parties: list[str]) -> str:
    raw_name = raw_name.strip()
    if not raw_name:
        return "$UNKNOWN$"

    canonical = _canonical_known_party(raw_name, known_issuing_parties)
    if canonical:
        return canonical

    raw_key = _simplify_company_name(raw_name)
    if not raw_key:
        return "$UNKNOWN$"

    exact_matches = {
        _simplify_company_name(known): known
        for known in known_issuing_parties
        if known != "$UNKNOWN$"
    }
    if raw_key in exact_matches:
        return exact_matches[raw_key]

    partial_matches = [
        known
        for known in known_issuing_parties
        if known != "$UNKNOWN$"
        and (raw_key in _simplify_company_name(known) or _simplify_company_name(known) in raw_key)
    ]
    if partial_matches:
        return max(partial_matches, key=len)

    return _slugify_company_name(raw_name)


def _parse_issuing_party_response(content: str) -> str | None:
    candidates = [content]
    extracted = _extract_json_from_response(content)
    if extracted != content:
        candidates.append(extracted)

    object_match = re.search(r"\{.*\}", extracted, re.DOTALL)
    if object_match:
        candidates.append(object_match.group(0))

    for candidate in candidates:
        try:
            result = json.loads(candidate)
        except Exception:
            continue
        if isinstance(result, dict):
            value = result.get("issuing_party")
            if isinstance(value, str) and value.strip():
                return value.strip()

    patterns = [
        r'"issuing_party"\s*:\s*"([^"]+)"',
        r"'issuing_party'\s*:\s*'([^']+)'",
        r'"issuing_party"\s*:\s*([A-Za-z0-9][A-Za-z0-9 .,&()/+-]*)',
    ]
    for pattern in patterns:
        match = re.search(pattern, extracted)
        if match:
            return match.group(1).strip().rstrip("},")

    return None


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
        return _heuristic_normalize_issuing_party(raw_name, known_issuing_parties)

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
            return _heuristic_normalize_issuing_party(raw_name, known_issuing_parties)
        parsed = _parse_issuing_party_response(content)
        if parsed:
            return _heuristic_normalize_issuing_party(parsed, known_issuing_parties)
    except Exception as exc:
        logger.error(f"NIF normalization failed: {exc}")

    return _heuristic_normalize_issuing_party(raw_name, known_issuing_parties)


get_system_prompt_raw_extraction = get_system_prompt_classify
