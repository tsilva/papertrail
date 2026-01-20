"""Dynamic enum loading and utilities."""

import json
import os
from enum import Enum
from pathlib import Path
from typing import Optional

from documentor.config import get_config_paths, get_current_profile


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


def _load_fallback_document_types() -> list[str]:
    """Load fallback document types from profile, config file, or use hardcoded defaults."""
    # Check profile first
    profile = get_current_profile()
    if profile and profile.document_types.fallback_list:
        types = list(profile.document_types.fallback_list)
        if "$UNKNOWN$" not in types:
            types.append("$UNKNOWN$")
        return sorted(types)

    # Try config file
    try:
        config_paths = get_config_paths()
        doc_types_path = config_paths.get("document_types")
        if doc_types_path and doc_types_path.exists():
            with open(doc_types_path, "r", encoding="utf-8") as f:
                types = json.load(f)
                if isinstance(types, list):
                    # Ensure $UNKNOWN$ is always present
                    if "$UNKNOWN$" not in types:
                        types.append("$UNKNOWN$")
                    return sorted(types)
    except Exception:
        pass

    # Hardcoded fallback if config file doesn't exist or fails to load
    return [
        "$UNKNOWN$", "bill", "certificate", "contract", "declaration", "email", "extract",
        "invoice", "letter", "notification", "other", "receipt", "report", "statement", "ticket"
    ]


def _load_fallback_issuing_parties() -> list[str]:
    """Load fallback issuing parties from profile or use hardcoded defaults."""
    # Check profile first
    profile = get_current_profile()
    if profile and profile.issuing_parties.fallback_list:
        parties = list(profile.issuing_parties.fallback_list)
        if "$UNKNOWN$" not in parties:
            parties.append("$UNKNOWN$")
        return sorted(parties)

    # Hardcoded fallback
    return [
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
