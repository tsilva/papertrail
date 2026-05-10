"""Default reconciliation policy values loaded from bundled configuration.

The bundled values are generic defaults. Profile-specific aliases, shared-period
rules, and business-specific overrides should live in profile policy files or
the profile ``reconciliation`` section.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _load_policy() -> dict[str, Any]:
    policy_path = Path(__file__).with_name("reconciliation_policy.yaml")
    with open(policy_path, "r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid reconciliation policy file: {policy_path}")
    return data


def _tuple(key: str) -> tuple[str, ...]:
    return tuple(str(item) for item in _POLICY.get(key, ()))


def _dict(key: str) -> dict[str, Any]:
    value = _POLICY.get(key, {})
    return dict(value) if isinstance(value, dict) else {}


_POLICY = _load_policy()

DEFAULT_AMOUNT_TOLERANCE = float(_POLICY.get("amount_tolerance", 0.01))
DEFAULT_DATE_WINDOW_DAYS = int(_POLICY.get("date_window_days", 30))
DEFAULT_TAX_NUMBER_DEFAULT_COUNTRY_PREFIX = str(
    _POLICY.get("tax_number_default_country_prefix", "PT")
)
DEFAULT_BANK_GENERATED_DOC_TYPES = _tuple("bank_generated_doc_types")
DEFAULT_STATEMENT_BANK_SCOPED_DOC_TYPES = _tuple("statement_bank_scoped_doc_types")
DEFAULT_STATEMENT_BANK_ISSUER_ALIASES = _dict("statement_bank_issuer_aliases")
DEFAULT_BANK_EXPORT_PREFIX = str(_POLICY.get("bank_export_prefix", "BNC_"))
DEFAULT_SUPPORTING_EXPORT_PREFIXES = _tuple("supporting_export_prefixes")
DEFAULT_SUPPORTING_DOC_TYPE_PATTERNS = _tuple("supporting_doc_type_patterns")
DEFAULT_DOCUMENT_FAMILIES = _dict("document_families")
DEFAULT_BANK_COUNTERPARTIES = _tuple("bank_counterparties")
DEFAULT_COUNTERPARTY_ALIASES = _dict("counterparty_aliases")
DEFAULT_SHARED_PERIOD_TRANSACTION_KEYWORDS = {
    party: tuple(keywords)
    for party, keywords in _dict("shared_period_transaction_keywords").items()
}
DEFAULT_SHARED_PERIOD_TITLE_TERMS = {
    party: tuple(terms)
    for party, terms in _dict("shared_period_title_terms").items()
}
DEFAULT_SAME_MONTH_SHARED_RULE_NAMES = _tuple("same_month_shared_rule_names")
DEFAULT_STRICT_STATEMENT_BANKS = _tuple("strict_statement_banks")
DEFAULT_SUPPORTING_PAIR_EXEMPT_STATEMENT_BANKS = _tuple(
    "supporting_pair_exempt_statement_banks"
)
DEFAULT_SHARED_PERIOD_LINK_CATEGORIES = _tuple("shared_period_link_categories")
DEFAULT_SHARED_PERIOD_SUPPLIER_EVIDENCE_ERROR_EXEMPT_RULE_NAMES = _tuple(
    "shared_period_supplier_evidence_error_exempt_rule_names"
)
DEFAULT_SHARED_PERIOD_BANK_ANCHOR_ERROR_EXEMPT_RULE_NAMES = _tuple(
    "shared_period_bank_anchor_error_exempt_rule_names"
)
DEFAULT_EVIDENCE_COUNTERPARTY_CATEGORIES = _tuple("evidence_counterparty_categories")
DEFAULT_EVIDENCE_COUNTERPARTY_REQUIRED_PATTERN = str(
    _POLICY.get("evidence_counterparty_required_pattern", "invoice")
)
DEFAULT_EVIDENCE_COUNTERPARTY_SKIP_IF_SUPPLIER_PRESENT_CATEGORIES = _tuple(
    "evidence_counterparty_skip_if_supplier_present_categories"
)
DEFAULT_EVIDENCE_COUNTERPARTY_AMOUNT_OPTIONAL_CATEGORIES = _tuple(
    "evidence_counterparty_amount_optional_categories"
)
DEFAULT_RECONCILIATION_CURRENCY = str(_POLICY.get("default_currency", "EUR"))
DEFAULT_LLM_MATCH_CONFIDENCE = float(_POLICY.get("llm_default_confidence", 0.5))
DEFAULT_LINE_ITEM_CATEGORY_ALIASES = _dict("line_item_category_aliases")
DEFAULT_LINE_ITEM_EXTRACTORS = _dict("line_item_extractors")
DEFAULT_RECONCILIATION_RULES = tuple(_POLICY.get("rules", ()))
