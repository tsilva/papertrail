"""Dynamic enum loading and utilities."""

import json
import os
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

from papertrail.config import get_current_profile


def _load_mappings_canonicals(field: str) -> set[str]:
    """Load canonical values from mappings.yaml for a given field.

    Args:
        field: Section name in mappings.yaml (e.g. 'document_types' or 'issuing_parties')

    Returns:
        Set of canonical values for the field.
    """
    profile = get_current_profile()
    if profile and profile.profile_dir:
        config_paths = [profile.profile_dir / "mappings.yaml"]
    else:
        config_paths = [Path(__file__).parent.parent / "profiles" / "default" / "mappings.yaml"]

    values = set()

    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    mappings = yaml.safe_load(f)

                if mappings and field in mappings:
                    field_data = mappings[field]
                    if "mappings" in field_data:
                        values.update(field_data["mappings"].values())

                break  # Found and loaded, stop searching
            except Exception:
                continue

    return values


# ============================================================================
# Lazy Loading Cache
# ============================================================================

_DOCUMENT_TYPES_LIST: list[str] | None = None
_ISSUING_PARTIES_LIST: list[str] | None = None


def reset_enum_cache() -> None:
    """Reset the enum cache, forcing re-evaluation on next access."""
    global _DOCUMENT_TYPES_LIST, _ISSUING_PARTIES_LIST
    _DOCUMENT_TYPES_LIST = None
    _ISSUING_PARTIES_LIST = None


# ============================================================================
# Utilities
# ============================================================================


def clean_enum_string(value: str, enum_prefix: Optional[str] = None) -> str:
    """
    Remove enum prefix from serialized enum strings.

    Handles formats like "DocumentType.invoice" -> "invoice"
    """
    if not isinstance(value, str):
        return value

    if enum_prefix:
        prefix = f"{enum_prefix}."
        if value.startswith(prefix):
            return value.split(".", 1)[-1]
    elif "." in value and value.count(".") == 1:
        return value.split(".", 1)[-1]

    return value


def create_dynamic_enum(name: str, values: list[str]) -> Enum:
    """Create a dynamic Enum class from a list of values."""
    return Enum(name, dict([(k, k) for k in values]), type=str)


# ============================================================================
# Hardcoded Fallbacks
# ============================================================================

FALLBACK_DOCUMENT_TYPES = [
    "$UNKNOWN$", "bank-note", "bank-statement", "contract", "contract-signup",
    "finance-balance", "finance-income", "insurance-auto", "insurance-notice",
    "invoice", "invoice-credit", "invoice-receipt", "notice", "notice-request",
    "other", "payroll-salary", "payroll-social", "payroll-vacation", "receipt",
    "tax-declaration", "tax-irs", "tax-vat"
]

FALLBACK_ISSUING_PARTIES = [
    "$UNKNOWN$", "ActivoBank", "Allianz", "Amazon", "Anthropic", "Antonio Martins & Filhos",
    "Apple", "Armando", "Ascendi", "AT", "Auchan", "Banco BEST", "Banco Invest",
    "Bandicam", "BIG", "Bitwarden", "BlackRock", "BP", "BPI", "Caetano Formula",
    "Carrefour", "CEPSA", "Cleverbridge", "Codota", "Cohere", "Coinbase",
    "Consensus", "Continente", "CTT", "Dacia", "DEGIRO", "Digital River",
    "DigitalOcean", "DOKKER", "E.Leclerc", "EUROPA", "ExpressVPN", "FGCT",
    "Fidelidade", "Fluxe", "Fundo de Compensacao do Trabalho", "Galp", "GESPOST",
    "GitHub", "GONCALTEAM", "Google", "Google Commerce Limited", "Government",
    "GRUPO", "HONG KONG USGREEN LIMITED", "INE", "Intermarche", "International",
    "IRN", "IRS", "iServices", "iShares", "justETF", "Justica",
    "La Maison", "Leroy", "LuLuComfort", "LusoAloja", "M2030",
    "MANUEL ALVES DIAS, LDA", "MB WAY", "Melo, Nadais & Associados", "Microsoft",
    "MillenniumBCP", "Mini Soninha", "Ministerio das Financas", "Mobatek",
    "MONTEPIO", "Multibanco", "Multicare", "MyCommerce", "MyFactoryHub", "NordVPN",
    "NOS", "Notario", "NTI", "OCC", "OpenAI", "OpenRouter", "OUYINEN", "Paddle",
    "Parallels", "PayPal", "PCDIGA", "Pinecone", "PLIMAT", "Pluxee", "PRIO",
    "PRISMXR", "Puzzle Message, Unipessoal Lda.", "Quindi", "Redunicre",
    "RegistoLEI", "Renault", "Republica Portuguesa", "RescueTime", "Restaurant",
    "Securitas", "Seguranca Social", "Shenzhen", "Sierra",
    "Sodexo", "Solred", "SONAE", "SRS Acquiom", "Swappie", "Sweatcoin",
    "Tesouraria", "TIAGO", "Tilda", "Together.ai", "TopazLabs", "Universal",
    "Universo", "Vanguard", "Via Verde", "VIDRIO PAIS PORTUGAL",
    "VITALOPE", "Vodafone", "WisdomTree", "Worten", "xAI"
]


# ============================================================================
# Main Loaders
# ============================================================================


def _load_enum_values(
    processed_files_dir: Optional[str],
    profile_predefined_attr: str,
    fallback_list: list[str],
    json_field: str,
    enum_prefix: str,
    mappings_field: str,
) -> list[str]:
    """Shared loader for document types and issuing parties.

    Args:
        processed_files_dir: Path to processed files directory.
        profile_predefined_attr: Attribute name on profile (e.g. 'document_types' or 'issuing_parties').
        fallback_list: Hardcoded fallback values.
        json_field: Key to read from metadata JSON files.
        enum_prefix: Prefix for clean_enum_string (e.g. 'DocumentType').
        mappings_field: Section name in mappings.yaml (e.g. 'document_types' or 'issuing_parties').

    Returns:
        Sorted list of enum values.
    """
    profile = get_current_profile()

    # Check profile for predefined values
    if profile:
        predefined = getattr(profile, profile_predefined_attr, None)
        if predefined is not None and predefined.predefined is not None:
            values = list(predefined.predefined)
            if "$UNKNOWN$" not in values:
                values.append("$UNKNOWN$")
            return sorted(values)

    # Determine processed_files_dir
    if processed_files_dir is None:
        if profile and profile.paths.processed:
            processed_files_dir = profile.paths.processed
        else:
            processed_files_dir = os.getenv("PROCESSED_FILES_DIR")

    # Start with fallback values
    values_set = set(fallback_list)

    # Scan processed files for dynamic values if path exists
    if processed_files_dir and Path(processed_files_dir).exists():
        processed_path = Path(processed_files_dir)

        for json_file in processed_path.rglob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    value = data.get(json_field)
                    if value and isinstance(value, str):
                        value = clean_enum_string(value, enum_prefix)
                        values_set.add(value)
            except Exception:
                continue

    # Merge in canonical values from mappings.yaml
    values_set.update(_load_mappings_canonicals(mappings_field))

    values_set.add("$UNKNOWN$")
    return sorted(values_set)


def load_document_types(processed_files_dir: Optional[str] = None) -> list[str]:
    """Load document types from profile predefined, processed files, or fallback."""
    return _load_enum_values(
        processed_files_dir, "document_types", FALLBACK_DOCUMENT_TYPES,
        "document_type", "DocumentType", "document_types",
    )


def load_issuing_parties(processed_files_dir: Optional[str] = None) -> list[str]:
    """Load issuing parties from profile predefined, processed files, or fallback."""
    return _load_enum_values(
        processed_files_dir, "issuing_parties", FALLBACK_ISSUING_PARTIES,
        "issuing_party", "IssuingParty", "issuing_parties",
    )


# Convenience functions with caching for fast repeated access
def get_document_types() -> list[str]:
    """Get document types list (cached after first call)."""
    global _DOCUMENT_TYPES_LIST
    if _DOCUMENT_TYPES_LIST is None:
        _DOCUMENT_TYPES_LIST = load_document_types()
    return _DOCUMENT_TYPES_LIST


def get_issuing_parties() -> list[str]:
    """Get issuing parties list (cached after first call)."""
    global _ISSUING_PARTIES_LIST
    if _ISSUING_PARTIES_LIST is None:
        _ISSUING_PARTIES_LIST = load_issuing_parties()
    return _ISSUING_PARTIES_LIST
