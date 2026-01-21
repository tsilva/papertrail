#!/usr/bin/env python3
"""
Migrate document types in metadata files and rename corresponding PDFs.

This script updates document_type values in JSON metadata files and renames
both JSON and PDF files to reflect the new document type naming conventions.

Usage:
    python scripts/migrate_document_types.py --processed-dir <path> [--dry-run] [--log-file <path>]

Example:
    python scripts/migrate_document_types.py \\
        --processed-dir "/path/to/processed/" \\
        --dry-run
"""

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from tqdm import tqdm

# Migration mapping: old document_type -> new document_type
MIGRATION_MAP = {
    "employee-salary-slip": "salary-slip",
    "employee-vacation-pay-slip": "vacation-pay-slip",
    "bank-stock-purchase-costs": "bank-stock-purchase",
    "bank-stock-sale-costs": "bank-stock-sale",
    "insurance-contract-signup": "signup-contract",
    "credit-contract-signup": "signup-contract",
}


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


def migrate_metadata_file(json_path: Path, mapping: Dict[str, str], dry_run: bool = False) -> Optional[Dict]:
    """
    Update document_type in JSON metadata file.

    Args:
        json_path: Path to JSON metadata file
        mapping: Migration mapping dictionary
        dry_run: If True, don't actually write changes

    Returns:
        Dictionary with migration info or None if no migration needed:
        {
            'old_type': str,
            'new_type': str,
            'json_path': Path,
            'success': bool,
            'error': Optional[str]
        }
    """
    try:
        # Read JSON metadata
        with open(json_path, 'r', encoding='utf-8') as f:
            metadata = json.load(f)

        old_type = metadata.get('document_type')

        # Check if migration needed
        if old_type not in mapping:
            return None

        new_type = mapping[old_type]

        # Update document_type (preserve document_type_raw!)
        metadata['document_type'] = new_type
        metadata['update_date'] = datetime.now().isoformat()

        # Write back to JSON
        if not dry_run:
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

        return {
            'old_type': old_type,
            'new_type': new_type,
            'json_path': json_path,
            'success': True,
            'error': None
        }

    except Exception as e:
        return {
            'old_type': old_type if 'old_type' in locals() else 'unknown',
            'new_type': mapping.get(old_type, 'unknown') if 'old_type' in locals() else 'unknown',
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
    if new_json.exists():
        return False, f"Target JSON already exists: {new_json}"
    if pdf_exists and new_pdf.exists():
        return False, f"Target PDF already exists: {new_pdf}"

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
        description='Migrate document types in metadata files and rename corresponding PDFs'
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
        default='migration_log.txt',
        help='Path to log file (default: migration_log.txt)'
    )

    args = parser.parse_args()

    processed_dir = Path(args.processed_dir)
    if not processed_dir.exists():
        print(f"Error: Processed directory not found: {processed_dir}")
        return 1

    # Open log file
    log_file = Path(args.log_file)
    log = open(log_file, 'w', encoding='utf-8')

    # Log header
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    log.write(f"Document Type Migration Log\n")
    log.write(f"Started: {timestamp}\n")
    log.write(f"Mode: {'DRY RUN' if args.dry_run else 'LIVE'}\n")
    log.write(f"Processed directory: {processed_dir}\n")
    log.write(f"\n{'='*80}\n\n")

    # Find all JSON files
    all_json_files = list(processed_dir.glob("*.json"))
    print(f"Found {len(all_json_files)} JSON files in {processed_dir}")

    # Filter to files needing migration
    files_to_migrate = []
    for json_file in tqdm(all_json_files, desc="Scanning files"):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)
            document_type = metadata.get('document_type')
            if document_type in MIGRATION_MAP:
                files_to_migrate.append(json_file)
        except Exception as e:
            log.write(f"[ERROR] Failed to read {json_file}: {e}\n")

    print(f"\nFound {len(files_to_migrate)} files requiring migration")

    if len(files_to_migrate) == 0:
        print("No files to migrate!")
        log.write("No files to migrate.\n")
        log.close()
        return 0

    # Count by type
    type_counts = {}
    for json_file in files_to_migrate:
        with open(json_file, 'r', encoding='utf-8') as f:
            metadata = json.load(f)
        document_type = metadata.get('document_type')
        type_counts[document_type] = type_counts.get(document_type, 0) + 1

    print("\nFiles to migrate by type:")
    for old_type, count in sorted(type_counts.items()):
        new_type = MIGRATION_MAP[old_type]
        print(f"  {old_type} -> {new_type}: {count} files")
        log.write(f"Migration: {old_type} -> {new_type}: {count} files\n")

    log.write(f"\n{'='*80}\n\n")

    # Perform migration
    stats = {
        'total': len(files_to_migrate),
        'success': 0,
        'errors': 0,
        'warnings': 0,
        'by_type': {}
    }

    print(f"\n{'DRY RUN - No changes will be made' if args.dry_run else 'Starting migration...'}\n")

    for json_file in tqdm(files_to_migrate, desc="Migrating files"):
        # Migrate metadata
        migration_info = migrate_metadata_file(json_file, MIGRATION_MAP, args.dry_run)

        if not migration_info:
            continue

        if not migration_info['success']:
            stats['errors'] += 1
            log.write(f"[ERROR] Failed to migrate {json_file}: {migration_info['error']}\n")
            continue

        old_type = migration_info['old_type']
        new_type = migration_info['new_type']

        # Track by type
        if old_type not in stats['by_type']:
            stats['by_type'][old_type] = {'success': 0, 'errors': 0}

        # Parse old filename
        old_filename = json_file.name
        components = parse_filename(old_filename)

        if not components:
            stats['errors'] += 1
            stats['by_type'][old_type]['errors'] += 1
            log.write(f"[ERROR] Failed to parse filename: {old_filename}\n")
            continue

        # Update document_type in components
        components['document_type'] = new_type

        # Build new filename
        new_filename = build_filename(components)
        new_json_path = json_file.parent / new_filename

        # Rename files
        success, error = rename_files(json_file, new_json_path, args.dry_run)

        if success:
            stats['success'] += 1
            stats['by_type'][old_type]['success'] += 1

            # Log success
            log.write(f"[{timestamp}] MIGRATED: {old_type} -> {new_type}\n")
            log.write(f"  Old JSON: {old_filename}\n")
            log.write(f"  New JSON: {new_filename}\n")

            old_pdf = json_file.with_suffix('.pdf')
            new_pdf = new_json_path.with_suffix('.pdf')
            log.write(f"  Old PDF:  {old_pdf.name}\n")
            log.write(f"  New PDF:  {new_pdf.name}\n")
            log.write(f"  Status: SUCCESS\n\n")
        else:
            stats['errors'] += 1
            stats['by_type'][old_type]['errors'] += 1
            log.write(f"[ERROR] Failed to rename files: {error}\n")
            log.write(f"  File: {old_filename}\n\n")

    # Write summary
    log.write(f"\n{'='*80}\n")
    log.write(f"MIGRATION SUMMARY\n")
    log.write(f"{'='*80}\n\n")
    log.write(f"Total files scanned: {len(all_json_files)}\n")
    log.write(f"Files requiring migration: {stats['total']}\n")
    log.write(f"Successful migrations: {stats['success']}\n")
    log.write(f"Errors: {stats['errors']}\n")
    log.write(f"\nBy type:\n")

    for old_type in sorted(stats['by_type'].keys()):
        new_type = MIGRATION_MAP[old_type]
        success_count = stats['by_type'][old_type]['success']
        error_count = stats['by_type'][old_type]['errors']
        log.write(f"  {old_type} -> {new_type}:\n")
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
    print(f"Files requiring migration: {stats['total']}")
    print(f"Successful migrations: {stats['success']}")
    print(f"Errors: {stats['errors']}")
    print(f"\nBy type:")

    for old_type in sorted(stats['by_type'].keys()):
        new_type = MIGRATION_MAP[old_type]
        success_count = stats['by_type'][old_type]['success']
        error_count = stats['by_type'][old_type]['errors']
        print(f"  {old_type} -> {new_type}:")
        print(f"    Success: {success_count}")
        print(f"    Errors: {error_count}")

    print(f"\nLog written to: {log_file}")

    if args.dry_run:
        print("\n*** DRY RUN - No changes were made ***")

    return 0 if stats['errors'] == 0 else 1


if __name__ == '__main__':
    exit(main())
