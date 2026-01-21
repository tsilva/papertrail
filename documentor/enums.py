"""Dynamic enum loading and utilities."""

import json
import os
from enum import Enum
from pathlib import Path
from typing import Optional

from documentor.config import get_config_paths, get_current_profile


# ============================================================================
# Lazy Loading Cache
# ============================================================================

# Module-level cache for lazy-loaded values
_DOCUMENT_TYPES: list[str] | None = None
_ISSUING_PARTIES: list[str] | None = None
_DOCUMENT_TYPE_ENUM: Enum | None = None
_ISSUING_PARTY_ENUM: Enum | None = None


def reset_enum_cache() -> None:
    """
    Reset the enum cache, forcing re-evaluation on next access.

    Call this after setting a new profile to ensure enums reflect
    the profile's predefined values.
    """
    global _DOCUMENT_TYPES, _ISSUING_PARTIES, _DOCUMENT_TYPE_ENUM, _ISSUING_PARTY_ENUM
    _DOCUMENT_TYPES = None
    _ISSUING_PARTIES = None
    _DOCUMENT_TYPE_ENUM = None
    _ISSUING_PARTY_ENUM = None


def get_document_types() -> list[str]:
    """Get document types list, loading lazily if needed."""
    global _DOCUMENT_TYPES
    if _DOCUMENT_TYPES is None:
        _DOCUMENT_TYPES = load_document_types()
    return _DOCUMENT_TYPES


def get_issuing_parties() -> list[str]:
    """Get issuing parties list, loading lazily if needed."""
    global _ISSUING_PARTIES
    if _ISSUING_PARTIES is None:
        _ISSUING_PARTIES = load_issuing_parties()
    return _ISSUING_PARTIES


def get_document_type_enum() -> Enum:
    """Get DocumentType enum, creating lazily if needed."""
    global _DOCUMENT_TYPE_ENUM
    if _DOCUMENT_TYPE_ENUM is None:
        _DOCUMENT_TYPE_ENUM = create_dynamic_enum('DocumentType', get_document_types())
    return _DOCUMENT_TYPE_ENUM


def get_issuing_party_enum() -> Enum:
    """Get IssuingParty enum, creating lazily if needed."""
    global _ISSUING_PARTY_ENUM
    if _ISSUING_PARTY_ENUM is None:
        _ISSUING_PARTY_ENUM = create_dynamic_enum('IssuingParty', get_issuing_parties())
    return _ISSUING_PARTY_ENUM


def clean_enum_string(value: str, enum_prefix: Optional[str] = None) -> str:
    """
    Remove enum prefix from serialized enum strings.

    Handles formats like "DocumentType.invoice" -> "invoice"

    Args:
        value: The value to clean
        enum_prefix: Optional specific prefix to check (e.g., "DocumentType")

    Returns:
        Cleaned value without enum prefix
    """
    if not isinstance(value, str):
        return value

    if enum_prefix:
        prefix = f"{enum_prefix}."
        if value.startswith(prefix):
            return value.split(".", 1)[-1]
    elif "." in value and value.count(".") == 1:
        # Generic enum format detection
        return value.split(".", 1)[-1]

    return value


def load_enum_values(
    field_name: str,
    fallback_values: list[str],
    processed_files_dir: Optional[str] = None,
    enum_prefix: Optional[str] = None
) -> list[str]:
    """
    Generic enum value loader from processed metadata files or profile.

    First checks if the current profile has predefined values for this field.
    If not, scans all JSON metadata files in the processed directory and extracts
    unique values for the specified field.

    Args:
        field_name: The metadata field to extract (e.g., "document_type", "issuing_party")
        fallback_values: Default values to use if no files found
        processed_files_dir: Path to processed files directory (defaults to profile or env var)
        enum_prefix: Optional enum prefix to strip (e.g., "DocumentType", "IssuingParty")

    Returns:
        Sorted list of unique values, always including "$UNKNOWN$"
    """
    profile = get_current_profile()

    # Check profile for predefined values first
    if profile:
        if field_name == "document_type" and profile.document_types.predefined is not None:
            values = list(profile.document_types.predefined)
            if "$UNKNOWN$" not in values:
                values.append("$UNKNOWN$")
            return sorted(values)
        elif field_name == "issuing_party" and profile.issuing_parties.predefined is not None:
            values = list(profile.issuing_parties.predefined)
            if "$UNKNOWN$" not in values:
                values.append("$UNKNOWN$")
            return sorted(values)

        # Use profile's processed_files_dir if not explicitly provided
        if processed_files_dir is None and profile.paths.processed:
            processed_files_dir = profile.paths.processed

    if processed_files_dir is None:
        processed_files_dir = os.getenv("PROCESSED_FILES_DIR")

    if not processed_files_dir or not Path(processed_files_dir).exists():
        return fallback_values

    values_set = set()
    processed_path = Path(processed_files_dir)

    # Scan all .json files in the processed directory
    for json_file in processed_path.rglob("*.json"):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                value = data.get(field_name)
                if value and isinstance(value, str):
                    value = clean_enum_string(value, enum_prefix)
                    values_set.add(value)
        except Exception:
            # Silently skip files that can't be read or parsed
            continue

    # If no values found, use fallback
    if not values_set:
        values_set = set(fallback_values)

    # Always ensure "$UNKNOWN$" is in the list
    values_set.add("$UNKNOWN$")

    # Return sorted list for consistency
    return sorted(values_set)


def create_dynamic_enum(name: str, values: list[str]) -> Enum:
    """
    Create a dynamic Enum class from a list of values.

    Args:
        name: Name of the enum class
        values: List of enum values (each value becomes both name and value)

    Returns:
        Dynamic Enum class
    """
    return Enum(name, dict([(k, k) for k in values]), type=str)


def _load_fallback_values(
    profile_accessor,
    config_key: Optional[str],
    hardcoded_defaults: list[str]
) -> list[str]:
    """
    Generic fallback loader for enum values.

    Args:
        profile_accessor: Function that takes profile and returns fallback_list or None
        config_key: Config path key to try (e.g., "document_types"), or None to skip
        hardcoded_defaults: Default values if all else fails

    Returns:
        Sorted list of values, always including "$UNKNOWN$"
    """
    # Check profile first
    profile = get_current_profile()
    if profile:
        fallback_list = profile_accessor(profile)
        if fallback_list:
            values = list(fallback_list)
            if "$UNKNOWN$" not in values:
                values.append("$UNKNOWN$")
            return sorted(values)

    # Try config file if key provided
    if config_key:
        try:
            config_paths = get_config_paths()
            config_path = config_paths.get(config_key)
            if config_path and config_path.exists():
                with open(config_path, "r", encoding="utf-8") as f:
                    values = json.load(f)
                    if isinstance(values, list):
                        if "$UNKNOWN$" not in values:
                            values.append("$UNKNOWN$")
                        return sorted(values)
        except Exception:
            pass

    # Return hardcoded defaults
    return hardcoded_defaults


def _load_fallback_document_types() -> list[str]:
    """Load fallback document types from profile, config file, or use hardcoded defaults."""
    return _load_fallback_values(
        profile_accessor=lambda p: p.document_types.fallback_list,
        config_key="document_types",
        hardcoded_defaults=[
            "$UNKNOWN$", "bill", "certificate", "contract", "declaration", "email", "extract",
            "invoice", "letter", "notification", "other", "receipt", "report", "statement", "ticket"
        ]
    )


def _load_fallback_issuing_parties() -> list[str]:
    """Load fallback issuing parties from profile or use hardcoded defaults."""
    return _load_fallback_values(
        profile_accessor=lambda p: p.issuing_parties.fallback_list,
        config_key=None,
        hardcoded_defaults=[
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
    )


# Fallback values for document types (loaded from profile or config/document_types.json if available)
FALLBACK_DOCUMENT_TYPES = _load_fallback_document_types()

# Fallback values for issuing parties (loaded from profile if available)
FALLBACK_ISSUING_PARTIES = _load_fallback_issuing_parties()


def load_document_types(processed_files_dir: Optional[str] = None) -> list[str]:
    """Load document types from processed files or use fallback."""
    return load_enum_values(
        field_name="document_type",
        fallback_values=FALLBACK_DOCUMENT_TYPES,
        processed_files_dir=processed_files_dir,
        enum_prefix="DocumentType"
    )


def load_issuing_parties(processed_files_dir: Optional[str] = None) -> list[str]:
    """Load issuing parties from processed files or use fallback."""
    return load_enum_values(
        field_name="issuing_party",
        fallback_values=FALLBACK_ISSUING_PARTIES,
        processed_files_dir=processed_files_dir,
        enum_prefix="IssuingParty"
    )
