"""Dynamic enum loading and utilities."""

import json
import os
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml

from papertrail.config import get_current_profile


def _load_mappings_canonicals() -> tuple[set[str], set[str]]:
    """Load canonical values from mappings.yaml.

    Returns:
        Tuple of (document_type_canonicals, issuing_party_canonicals)
    """
    # Find mappings.yaml in config directory
    config_paths = [
        Path(__file__).parent.parent / "config" / "mappings.yaml",
        Path.cwd() / "config" / "mappings.yaml",
    ]

    doc_types = set()
    issuing_parties = set()

    for config_path in config_paths:
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    mappings = yaml.safe_load(f)

                # Get document_types canonicals
                if mappings and "document_types" in mappings:
                    canonicals = mappings["document_types"].get("canonicals", [])
                    if canonicals:
                        doc_types.update(canonicals)
                    # Also include values from confirmed and auto mappings
                    for section in ["confirmed", "auto"]:
                        if section in mappings["document_types"]:
                            doc_types.update(mappings["document_types"][section].values())

                # Get issuing_parties canonicals
                if mappings and "issuing_parties" in mappings:
                    canonicals = mappings["issuing_parties"].get("canonicals", [])
                    if canonicals:
                        issuing_parties.update(canonicals)
                    # Also include values from confirmed and auto mappings
                    for section in ["confirmed", "auto"]:
                        if section in mappings["issuing_parties"]:
                            issuing_parties.update(mappings["issuing_parties"][section].values())

                break  # Found and loaded, stop searching
            except Exception:
                continue

    return doc_types, issuing_parties


# ============================================================================
# Lazy Loading Cache
# ============================================================================

_DOCUMENT_TYPE_ENUM: Enum | None = None
_ISSUING_PARTY_ENUM: Enum | None = None
_DOCUMENT_TYPES_LIST: list[str] | None = None
_ISSUING_PARTIES_LIST: list[str] | None = None


def reset_enum_cache() -> None:
    """Reset the enum cache, forcing re-evaluation on next access."""
    global _DOCUMENT_TYPE_ENUM, _ISSUING_PARTY_ENUM, _DOCUMENT_TYPES_LIST, _ISSUING_PARTIES_LIST
    _DOCUMENT_TYPE_ENUM = None
    _ISSUING_PARTY_ENUM = None
    _DOCUMENT_TYPES_LIST = None
    _ISSUING_PARTIES_LIST = None


def get_document_type_enum() -> Enum:
    """Get DocumentType enum, creating lazily if needed."""
    global _DOCUMENT_TYPE_ENUM
    if _DOCUMENT_TYPE_ENUM is None:
        _DOCUMENT_TYPE_ENUM = create_dynamic_enum('DocumentType', load_document_types())
    return _DOCUMENT_TYPE_ENUM


def get_issuing_party_enum() -> Enum:
    """Get IssuingParty enum, creating lazily if needed."""
    global _ISSUING_PARTY_ENUM
    if _ISSUING_PARTY_ENUM is None:
        _ISSUING_PARTY_ENUM = create_dynamic_enum('IssuingParty', load_issuing_parties())
    return _ISSUING_PARTY_ENUM


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
    "$UNKNOWN$", "bill", "certificate", "contract", "declaration", "email", "extract",
    "invoice", "letter", "notification", "other", "receipt", "report", "statement", "ticket"
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


def load_document_types(processed_files_dir: Optional[str] = None) -> list[str]:
    """
    Load document types from profile predefined, processed files, or fallback.

    Priority:
    1. Profile predefined values
    2. Dynamic loading from processed metadata files
    3. Hardcoded fallback list
    """
    profile = get_current_profile()

    # Check profile for predefined values
    if profile and profile.document_types.predefined is not None:
        values = list(profile.document_types.predefined)
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
    values_set = set(FALLBACK_DOCUMENT_TYPES)

    # Scan processed files for dynamic values if path exists
    if processed_files_dir and Path(processed_files_dir).exists():
        processed_path = Path(processed_files_dir)

        for json_file in processed_path.rglob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    value = data.get("document_type")
                    if value and isinstance(value, str):
                        value = clean_enum_string(value, "DocumentType")
                        values_set.add(value)
            except Exception:
                continue

    # Merge in canonical values from mappings.yaml
    doc_type_canonicals, _ = _load_mappings_canonicals()
    values_set.update(doc_type_canonicals)

    values_set.add("$UNKNOWN$")
    return sorted(values_set)


def load_issuing_parties(processed_files_dir: Optional[str] = None) -> list[str]:
    """
    Load issuing parties from profile predefined, processed files, or fallback.

    Priority:
    1. Profile predefined values
    2. Dynamic loading from processed metadata files
    3. Hardcoded fallback list
    """
    profile = get_current_profile()

    # Check profile for predefined values
    if profile and profile.issuing_parties.predefined is not None:
        values = list(profile.issuing_parties.predefined)
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
    values_set = set(FALLBACK_ISSUING_PARTIES)

    # Scan processed files for dynamic values if path exists
    if processed_files_dir and Path(processed_files_dir).exists():
        processed_path = Path(processed_files_dir)

        for json_file in processed_path.rglob("*.json"):
            try:
                with open(json_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    value = data.get("issuing_party")
                    if value and isinstance(value, str):
                        value = clean_enum_string(value, "IssuingParty")
                        values_set.add(value)
            except Exception:
                continue

    # Merge in canonical values from mappings.yaml
    _, issuing_party_canonicals = _load_mappings_canonicals()
    values_set.update(issuing_party_canonicals)

    values_set.add("$UNKNOWN$")
    return sorted(values_set)


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
