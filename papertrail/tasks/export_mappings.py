"""Export file mappings task.

Copies exported files to a subfolder with configurable prefixes based on
document metadata (e.g., bank statements get 'BNC_' prefix).
"""

import json
import shutil
from pathlib import Path
from typing import List, Optional

from papertrail.config import get_current_profile
from papertrail.console import get_console
from papertrail.hashing import hash_file_fast
from papertrail.logging_utils import get_logger
from papertrail.profiles import ExportMappingRule
from papertrail.tasks import task_log_context
from papertrail.tasks.organization import sanitize_filename_component

logger = get_logger('cli')


def _get_nested_value(metadata: dict, key: str):
    """Get a value from metadata, supporting dot notation for nested keys.

    Args:
        metadata: Document metadata dictionary.
        key: Key to lookup, supports dot notation (e.g., "qrcode.qr_type").

    Returns:
        Value if found, None otherwise.
    """
    if '.' not in key:
        return metadata.get(key)

    parts = key.split('.')
    value = metadata
    for part in parts:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
        if value is None:
            return None
    return value


def _match_value(actual, pattern: str) -> bool:
    """Match a value against a pattern.

    Args:
        actual: Actual value from metadata.
        pattern: Pattern to match. Supports:
            - Exact match: "invoice"
            - Prefix match: "bank-*" (matches "bank-statement", "bank-transfer", etc.)
            - Numeric match: "0" matches 0.0 (float/int values compared numerically)

    Returns:
        True if matches, False otherwise.
    """
    if actual is None:
        return False

    if pattern.endswith('*'):
        prefix = pattern[:-1]
        return str(actual).startswith(prefix)

    if isinstance(actual, (int, float)):
        try:
            return actual == float(pattern)
        except (ValueError, TypeError):
            return False

    return actual == pattern


def evaluate_mapping_rules(metadata: dict, rules: List[ExportMappingRule]) -> Optional[str]:
    """Return prefix if any rule matches, None otherwise.

    Args:
        metadata: Document metadata dictionary.
        rules: List of mapping rules to evaluate.

    Returns:
        Prefix string if a rule matches, None otherwise.
    """
    for rule in rules:
        if all(_match_value(_get_nested_value(metadata, k), v) for k, v in rule.match.items()):
            return rule.prefix
    return None


def _build_filename_from_fields(metadata: dict, fields: List[str]) -> str:
    """Build a filename from selected metadata fields.

    Args:
        metadata: Document metadata dictionary.
        fields: List of field names to include (e.g., ["date_issued", "issuing_party"]).

    Returns:
        Sanitized filename string with hash suffix and .pdf extension.
    """
    parts = []
    for field in fields:
        if field == "total_amount":
            amount = metadata.get("total_amount")
            if amount is not None:
                amount_str = f"{amount:.0f}" if float(amount) == int(amount) else f"{amount}"
                currency = metadata.get("total_amount_currency") or ""
                parts.append(sanitize_filename_component(f"{amount_str} {currency}".strip()))
            continue

        value = metadata.get(field)
        if value and str(value).strip():
            parts.append(sanitize_filename_component(str(value)))

    # Always append hash for uniqueness
    file_hash = metadata.get("hash_content", "")
    parts.append(f"{file_hash}.pdf")

    return " - ".join(parts).lower()


def task_apply_export_mappings(
    export_path: Path,
    dry_run: bool = False,
) -> dict:
    """Apply export file mappings to create remapped copies in subfolder.

    Args:
        export_path: Path to export folder (processes all date subfolders).
        dry_run: If True, show what would be done without copying.

    Returns:
        Dict with stats: {'processed': N, 'remapped': N, 'copied': N, 'skipped': N}
    """
    console = get_console()
    profile = get_current_profile()

    if not profile:
        console.error("No profile is active.", indent=False)
        return {'processed': 0, 'remapped': 0, 'copied': 0, 'skipped': 0}

    config = profile.export.file_mappings
    if not config.enabled:
        console.warning("Export file mappings not enabled in profile.", indent=False)
        return {'processed': 0, 'remapped': 0, 'copied': 0, 'skipped': 0}

    if not config.rules:
        console.warning("No export mapping rules configured.", indent=False)
        return {'processed': 0, 'remapped': 0, 'copied': 0, 'skipped': 0}

    stats = {'processed': 0, 'remapped': 0, 'copied': 0, 'skipped': 0}

    # Find all date subfolders (pattern: YYYY-MM)
    date_dirs = sorted([
        d for d in export_path.iterdir()
        if d.is_dir() and len(d.name) == 7 and d.name[4] == '-'
    ])

    if not date_dirs:
        console.warning(f"No date subfolders found in {export_path}", indent=False)
        return stats

    with task_log_context(export_path, "apply_export_mappings"):
        logger.debug(f"Processing {len(date_dirs)} date subfolder(s)")
        logger.debug(f"Output subfolder: {config.output_subfolder}")
        logger.debug(f"Rules: {len(config.rules)}")
        if dry_run:
            logger.debug("DRY RUN - no files will be copied")

        for date_dir in date_dirs:
            dir_stats = _process_date_folder(date_dir, config, dry_run)
            stats['processed'] += dir_stats['processed']
            stats['remapped'] += dir_stats['remapped']
            stats['copied'] += dir_stats['copied']
            stats['skipped'] += dir_stats['skipped']

        # Console output
        if dry_run:
            console.success(
                f"[DRY RUN] Would process {stats['processed']} files: "
                f"{stats['remapped']} remapped, {stats['copied']} copied as-is",
                indent=False
            )
        else:
            console.success(
                f"Processed {stats['processed']} files: "
                f"{stats['remapped']} remapped, {stats['copied']} copied as-is, "
                f"{stats['skipped']} skipped (already exist)",
                indent=False
            )

        logger.debug(f"Export mappings complete: {stats}")

    return stats


def _process_date_folder(
    date_dir: Path,
    config,
    dry_run: bool,
) -> dict:
    """Process a single date folder.

    Args:
        date_dir: Path to date folder (e.g., export/2025-01/).
        config: ExportFileMappingsConfig instance.
        dry_run: If True, don't actually copy files.

    Returns:
        Dict with stats for this folder.
    """
    stats = {'processed': 0, 'remapped': 0, 'copied': 0, 'skipped': 0}

    output_dir = date_dir / config.output_subfolder
    if not dry_run:
        output_dir.mkdir(parents=True, exist_ok=True)

    # Find all PDF files (excluding the output subfolder)
    pdf_files = [
        f for f in date_dir.iterdir()
        if f.is_file() and f.suffix.lower() == '.pdf'
    ]

    for pdf_path in pdf_files:
        json_path = pdf_path.with_suffix('.json')

        # Skip if no sidecar JSON
        if not json_path.exists():
            logger.debug(f"Skipping {pdf_path.name}: no sidecar JSON")
            continue

        # Load metadata
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
        except Exception as e:
            logger.warning(f"Failed to read metadata for {pdf_path.name}: {e}")
            continue

        # Evaluate rules (first match wins, then default_prefix as fallback)
        prefix = evaluate_mapping_rules(metadata, config.rules)
        if prefix is None:
            prefix = config.default_prefix

        # Compute base filename (custom fields or original)
        if config.filename_fields is not None:
            base_filename = _build_filename_from_fields(metadata, config.filename_fields)
        else:
            base_filename = pdf_path.name

        # Determine output filename
        if prefix:
            output_filename = f"{prefix}{base_filename}"
            stats['remapped'] += 1
        else:
            output_filename = base_filename
            stats['copied'] += 1

        output_path = output_dir / output_filename
        stats['processed'] += 1

        # Skip if already exists with same hash
        if output_path.exists():
            src_hash = hash_file_fast(pdf_path)
            dst_hash = hash_file_fast(output_path)
            if src_hash == dst_hash:
                logger.debug(f"Skipping {pdf_path.name}: already exists with same hash")
                stats['skipped'] += 1
                # Adjust counts since we're skipping
                if prefix:
                    stats['remapped'] -= 1
                else:
                    stats['copied'] -= 1
                continue

        # Copy file
        if dry_run:
            logger.debug(f"[DRY RUN] Would copy: {pdf_path.name} -> {output_filename}")
        else:
            shutil.copy2(pdf_path, output_path)
            logger.debug(f"Copied: {pdf_path.name} -> {output_filename}")

    return stats
