#!/usr/bin/env python3
"""
Migrate $UNKNOWN$ issuing parties in metadata files using mappings.yaml.

This script finds files where issuing_party is "$UNKNOWN$" but issuing_party_raw
has a known mapping in config/mappings.yaml, then updates both the JSON metadata
and renames the corresponding PDF files.

Usage:
    python scripts/migrate_issuing_parties.py --processed-dir <path> [--dry-run] [--log-file <path>]

Example:
    python scripts/migrate_issuing_parties.py \
        --processed-dir "/path/to/processed/" \
        --dry-run
"""

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from documentor.mappings import MappingsManager

UNKNOWN_SENTINEL = "$UNKNOWN$"


def parse_filename(filename: str) -> Optional[Dict[str, Optional[str]]]:
    """
    Parse filename into components.

    Filename format: YYYY-MM-DD - document-type - issuing-party - [service-name] - [amount currency] - hash.ext

    Args:
        filename: The filename to parse (with extension)

    Returns:
        Dictionary with components or None if parsing fails:
        {
            'date': str,
            'document_type': str,
            'issuing_party': str,
            'service_name': Optional[str],
            'amount': Optional[str],
            'currency': Optional[str],
            'hash': str,
            'ext': str
        }
    """
    # Remove extension
    name_without_ext = Path(filename).stem
    ext = Path(filename).suffix

    # Split by " - " (space-hyphen-space separator)
    parts = name_without_ext.split(" - ")

    if len(parts) < 4:
        return None

    # Extract fixed components
    date = parts[0]
    document_type = parts[1]
    issuing_party = parts[2]

    # Last part is always hash
    file_hash = parts[-1]

    # Middle parts (optional): could be service, or amount, or both
    # Amount pattern: \d+\.?\d* [a-z]{3}
    amount_pattern = re.compile(r'^(\d+\.?\d*)\s+([a-z]{3})$')

    service_name = None
    amount = None
    currency = None

    # Process middle parts (between issuing_party and hash)
    middle_parts = parts[3:-1]

    for part in middle_parts:
        match = amount_pattern.match(part)
        if match:
            amount = match.group(1)
            currency = match.group(2)
        else:
            # If not amount, it's service_name
            service_name = part

    return {
        'date': date,
        'document_type': document_type,
        'issuing_party': issuing_party,
        'service_name': service_name,
        'amount': amount,
        'currency': currency,
        'hash': file_hash,
        'ext': ext
    }


def build_filename(components: Dict[str, Optional[str]]) -> str:
    """
    Reconstruct filename from components.

    Args:
        components: Dictionary with filename components

    Returns:
        Reconstructed filename with extension
    """
    parts = [
        components['date'],
        components['document_type'],
        components['issuing_party']
    ]

    # Add optional service_name
    if components['service_name']:
        parts.append(components['service_name'])

    # Add optional amount and currency
    if components['amount'] and components['currency']:
        parts.append(f"{components['amount']} {components['currency']}")

    # Add hash
    parts.append(components['hash'])

    # Join with " - " and add extension
    filename = " - ".join(parts) + components['ext']

    return filename


def sanitize_for_filename(value: str) -> str:
    """
    Sanitize a value for use in filename.

    Converts to lowercase and replaces unsafe characters.

    Args:
        value: The value to sanitize

    Returns:
        Sanitized string safe for filenames
    """
    # Convert to lowercase
    result = value.lower()
    # Replace characters that are problematic in filenames
    result = result.replace("/", "-")
    result = result.replace("\\", "-")
    result = result.replace(":", "-")
    result = result.replace("*", "-")
    result = result.replace("?", "-")
    result = result.replace('"', "-")
    result = result.replace("<", "-")
    result = result.replace(">", "-")
    result = result.replace("|", "-")
    # Collapse multiple hyphens
    while "--" in result:
        result = result.replace("--", "-")
    return result.strip("-")


def migrate_metadata_file(
    json_path: Path,
    mappings: MappingsManager,
    dry_run: bool = False
) -> Optional[Dict]:
    """
    Update issuing_party in JSON metadata file using mappings.

    Args:
        json_path: Path to JSON metadata file
        mappings: MappingsManager instance with loaded mappings
        dry_run: If True, don't actually write changes

    Returns:
        Dictionary with migration info or None if no migration needed:
        {
            'old_value': str,
            'new_value': str,
            'raw_value': str,
            'json_path': Path,
            'success': bool,
            'error': Optional[str]
        }
    """
    try:
        # Read JSON metadata
        with open(json_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)

        issuing_party = metadata.get('issuing_party')
        issuing_party_raw = metadata.get('issuing_party_raw')

        # Check if migration needed
        if issuing_party != UNKNOWN_SENTINEL:
            return None

        if not issuing_party_raw:
            return {
                'old_value': issuing_party,
                'new_value': None,
                'raw_value': None,
                'json_path': json_path,
                'success': False,
                'error': 'No issuing_party_raw value found'
            }

        # Look up mapping
        canonical = mappings.get_mapping(issuing_party_raw, "issuing_parties")

        if not canonical:
            return {
                'old_value': issuing_party,
                'new_value': None,
                'raw_value': issuing_party_raw,
                'json_path': json_path,
                'success': False,
                'error': f'No mapping found for raw value: {issuing_party_raw}'
            }

        # Update issuing_party
        metadata['issuing_party'] = canonical
        metadata['update_date'] = datetime.now().isoformat()

        # Write back to JSON
        if not dry_run:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

        return {
            'old_value': issuing_party,
            'new_value': canonical,
            'raw_value': issuing_party_raw,
            'json_path': json_path,
            'success': True,
            'error': None
        }

    except Exception as e:
        return {
            'old_value': issuing_party if 'issuing_party' in locals() else 'unknown',
            'new_value': None,
            'raw_value': issuing_party_raw if 'issuing_party_raw' in locals() else None,
            'json_path': json_path,
            'success': False,
            'error': str(e)
        }


def rename_files(old_json: Path, new_json: Path, dry_run: bool = False) -> Tuple[bool, Optional[str]]:
    """
    Rename both JSON and PDF files.

    Args:
        old_json: Old JSON file path
        new_json: New JSON file path
        dry_run: If True, don't actually rename

    Returns:
        Tuple of (success: bool, error: Optional[str])
    """
    old_pdf = old_json.with_suffix('.pdf')
    new_pdf = new_json.with_suffix('.pdf')

    # Check old files exist
    if not old_json.exists():
        return False, f"JSON file not found: {old_json}"

    pdf_exists = old_pdf.exists()

    # Check new files don't exist (prevent overwrites)
    if new_json.exists() and new_json != old_json:
        return False, f"Target JSON already exists: {new_json}"
    if pdf_exists and new_pdf.exists() and new_pdf != old_pdf:
        return False, f"Target PDF already exists: {new_pdf}"

    # Skip if no rename needed
    if old_json == new_json:
        return True, None

    # Rename files
    if not dry_run:
        try:
            # Rename JSON
            old_json.rename(new_json)

            # Rename PDF if it exists
            if pdf_exists:
                old_pdf.rename(new_pdf)

        except Exception as e:
            # Attempt rollback if JSON was renamed but PDF failed
            if new_json.exists() and not old_json.exists():
                try:
                    new_json.rename(old_json)
                except:
                    pass
            return False, f"Rename failed: {e}"

    return True, None


def main():
    parser = argparse.ArgumentParser(
        description='Migrate $UNKNOWN$ issuing parties using mappings.yaml'
    )
    parser.add_argument(
        '--processed-dir',
        required=True,
        help='Path to processed directory containing JSON and PDF files'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help="Don't make changes, just report what would be done"
    )
    parser.add_argument(
        '--log-file',
        default='issuing_party_migration_log.txt',
        help='Path to log file (default: issuing_party_migration_log.txt)'
    )
    parser.add_argument(
        '--mappings-file',
        default=None,
        help='Path to mappings.yaml (default: config/mappings.yaml)'
    )

    args = parser.parse_args()

    processed_dir = Path(args.processed_dir)
    if not processed_dir.exists():
        print(f"Error: Processed directory not found: {processed_dir}")
        return 1

    # Load mappings
    if args.mappings_file:
        mappings_path = Path(args.mappings_file)
    else:
        mappings_path = Path(__file__).parent.parent / "config" / "mappings.yaml"

    if not mappings_path.exists():
        print(f"Error: Mappings file not found: {mappings_path}")
        return 1

    mappings = MappingsManager(mappings_path)

    # Show mappings stats
    stats = mappings.get_stats()
    print(f"Loaded mappings from {mappings_path}")
    print(f"  Issuing parties: {stats['issuing_parties']['confirmed']} confirmed, {stats['issuing_parties']['auto']} auto")

    # Open log file
    log_file = Path(args.log_file)
    log = open(log_file, 'w', encoding='utf-8')

    # Log header
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log.write(f"Issuing Party Migration Log\n")
    log.write(f"Started: {timestamp}\n")
    log.write(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}\n")
    log.write(f"Processed directory: {processed_dir}\n")
    log.write(f"Mappings file: {mappings_path}\n")
    log.write(f"\n{'='*80}\n\n")

    # Find all JSON files
    all_json_files = list(processed_dir.glob("*.json"))
    print(f"Found {len(all_json_files)} JSON files in {processed_dir}")

    # Filter to files needing migration (issuing_party == $UNKNOWN$)
    files_to_migrate = []
    files_no_raw = []
    files_no_mapping = []

    for json_file in tqdm(all_json_files, desc="Scanning files"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)

            issuing_party = metadata.get('issuing_party')
            issuing_party_raw = metadata.get('issuing_party_raw')

            if issuing_party == UNKNOWN_SENTINEL:
                if not issuing_party_raw:
                    files_no_raw.append(json_file)
                elif mappings.get_mapping(issuing_party_raw, "issuing_parties"):
                    files_to_migrate.append(json_file)
                else:
                    files_no_mapping.append((json_file, issuing_party_raw))

        except Exception as e:
            log.write(f"[ERROR] Failed to read {json_file}: {e}\n")

    print(f"\nFound {len(files_to_migrate)} files with $UNKNOWN$ that can be migrated")
    print(f"Found {len(files_no_raw)} files with $UNKNOWN$ but no raw value")
    print(f"Found {len(files_no_mapping)} files with $UNKNOWN$ and raw value but no mapping")

    # Log files without mappings
    if files_no_mapping:
        log.write("Files with $UNKNOWN$ but no mapping found:\n")
        raw_values_missing = {}
        for json_file, raw_value in files_no_mapping:
            log.write(f"  {json_file.name}: raw='{raw_value}'\n")
            raw_values_missing[raw_value] = raw_values_missing.get(raw_value, 0) + 1
        log.write(f"\nMissing mappings summary:\n")
        for raw_value, count in sorted(raw_values_missing.items(), key=lambda x: -x[1]):
            log.write(f"  '{raw_value}': {count} files\n")
        log.write(f"\n{'='*80}\n\n")

    if len(files_to_migrate) == 0:
        print("No files to migrate!")
        log.write("No files to migrate.\n")
        log.close()
        return 0

    # Perform migration
    migration_stats = {
        'total': len(files_to_migrate),
        'success': 0,
        'errors': 0,
        'by_canonical': {}
    }

    print(f"\n{'DRY RUN - No changes will be made' if args.dry_run else 'Starting migration...'}\n")

    for json_file in tqdm(files_to_migrate, desc="Migrating files"):
        # Migrate metadata
        migration_info = migrate_metadata_file(json_file, mappings, args.dry_run)

        if not migration_info:
            continue

        if not migration_info['success']:
            migration_stats['errors'] += 1
            log.write(f"[ERROR] Failed to migrate {json_file}: {migration_info['error']}\n")
            continue

        new_canonical = migration_info['new_value']
        raw_value = migration_info['raw_value']

        # Track by canonical
        if new_canonical not in migration_stats['by_canonical']:
            migration_stats['by_canonical'][new_canonical] = {'success': 0, 'errors': 0}

        # Parse old filename
        old_filename = json_file.name
        components = parse_filename(old_filename)

        if not components:
            migration_stats['errors'] += 1
            migration_stats['by_canonical'][new_canonical]['errors'] += 1
            log.write(f"[ERROR] Failed to parse filename: {old_filename}\n")
            continue

        # Update issuing_party in components (sanitized for filename)
        components['issuing_party'] = sanitize_for_filename(new_canonical)

        # Build new filename
        new_filename = build_filename(components)
        new_json_path = json_file.parent / new_filename

        # Rename files
        success, error = rename_files(json_file, new_json_path, args.dry_run)

        if success:
            migration_stats['success'] += 1
            migration_stats['by_canonical'][new_canonical]['success'] += 1

            # Log success
            log.write(f"[{timestamp}] MIGRATED: $UNKNOWN$ -> {new_canonical}\n")
            log.write(f"  Raw value: {raw_value}\n")
            log.write(f"  Old JSON: {old_filename}\n")
            log.write(f"  New JSON: {new_filename}\n")

            old_pdf = json_file.with_suffix('.pdf')
            new_pdf = new_json_path.with_suffix('.pdf')
            log.write(f"  Old PDF:  {old_pdf.name}\n")
            log.write(f"  New PDF:  {new_pdf.name}\n")
            log.write(f"  Status: SUCCESS\n\n")
        else:
            migration_stats['errors'] += 1
            migration_stats['by_canonical'][new_canonical]['errors'] += 1
            log.write(f"[ERROR] Failed to rename files: {error}\n")
            log.write(f"  File: {old_filename}\n\n")

    # Write summary
    log.write(f"\n{'='*80}\n")
    log.write(f"MIGRATION SUMMARY\n")
    log.write(f"{'='*80}\n\n")
    log.write(f"Total files scanned: {len(all_json_files)}\n")
    log.write(f"Files with $UNKNOWN$ issuing_party: {len(files_to_migrate) + len(files_no_raw) + len(files_no_mapping)}\n")
    log.write(f"  - With mapping available: {len(files_to_migrate)}\n")
    log.write(f"  - Without raw value: {len(files_no_raw)}\n")
    log.write(f"  - Without mapping: {len(files_no_mapping)}\n")
    log.write(f"\nMigration results:\n")
    log.write(f"  Successful migrations: {migration_stats['success']}\n")
    log.write(f"  Errors: {migration_stats['errors']}\n")
    log.write(f"\nBy canonical value:\n")

    for canonical in sorted(migration_stats['by_canonical'].keys()):
        success_count = migration_stats['by_canonical'][canonical]['success']
        error_count = migration_stats['by_canonical'][canonical]['errors']
        log.write(f"  {canonical}:\n")
        log.write(f"    Success: {success_count}\n")
        log.write(f"    Errors: {error_count}\n")

    end_timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log.write(f"\nCompleted: {end_timestamp}\n")
    log.close()

    # Print summary
    print(f"\n{'='*60}")
    print("MIGRATION SUMMARY")
    print(f"{'='*60}")
    print(f"Total files scanned: {len(all_json_files)}")
    print(f"Files with $UNKNOWN$ issuing_party: {len(files_to_migrate) + len(files_no_raw) + len(files_no_mapping)}")
    print(f"  - With mapping available: {len(files_to_migrate)}")
    print(f"  - Without raw value: {len(files_no_raw)}")
    print(f"  - Without mapping: {len(files_no_mapping)}")
    print(f"\nMigration results:")
    print(f"  Successful migrations: {migration_stats['success']}")
    print(f"  Errors: {migration_stats['errors']}")

    if migration_stats['by_canonical']:
        print(f"\nBy canonical value:")
        for canonical in sorted(migration_stats['by_canonical'].keys()):
            success_count = migration_stats['by_canonical'][canonical]['success']
            error_count = migration_stats['by_canonical'][canonical]['errors']
            print(f"  {canonical}: {success_count} success, {error_count} errors")

    print(f"\nLog written to: {log_file}")

    if args.dry_run:
        print("\n*** DRY RUN - No changes were made ***")

    return 0 if migration_stats['errors'] == 0 else 1


if __name__ == '__main__':
    exit(main())
