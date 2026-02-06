"""QR code inventory task - scan PDFs to inventory QR codes."""

import json
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml

from papertrail.console import get_console
from papertrail.logging_utils import get_logger, setup_task_logging
from papertrail.qr import extract_all_qr_codes, detect_qr_type, check_pyzbar_available
from papertrail.qr.models import QRCodeType, QRCodeData

logger = get_logger('qr_inventory')

# Maximum unique samples to store per QR type
MAX_SAMPLES_PER_TYPE = 20


@dataclass
class QRSample:
    """Sample QR code for inventory."""
    content: str
    file: str
    page: int
    # Type-specific fields
    domain: Optional[str] = None  # For URL types
    fields_extracted: list[str] = field(default_factory=list)  # For structured types
    pattern_guess: Optional[str] = None  # For unknown types


@dataclass
class IssuerStats:
    """QR stats for a single issuer."""
    with_qr: int = 0
    without_qr: int = 0


@dataclass
class InventoryResult:
    """Single PDF scan result."""
    pdf_path: str
    has_qr: bool
    qr_codes: list[dict] = field(default_factory=list)
    issuer: Optional[str] = None
    error: Optional[str] = None


def _extract_issuer_from_filename(filename: str) -> Optional[str]:
    """Extract issuing party from filename pattern: YYYY-MM-DD - type - issuer - ..."""
    # Pattern: date - type - issuer - rest
    match = re.match(r'\d{4}-\d{2}-\d{2}\s*-\s*[^-]+\s*-\s*([^-]+)', filename)
    if match:
        return match.group(1).strip().lower()
    return None


def _guess_pattern(content: str) -> str:
    """Guess the pattern type of unknown QR content."""
    content = content.strip()

    # Check for key-value patterns
    if '*' in content and ':' in content:
        return "key-value (asterisk-separated)"
    if '&' in content and '=' in content:
        return "query-string format"
    if '\n' in content:
        return "multi-line text"
    if content.isdigit():
        return "numeric only"
    if re.match(r'^[A-Za-z0-9+/=]+$', content) and len(content) > 20:
        return "possibly base64 encoded"
    if re.match(r'^[0-9A-Fa-f]+$', content) and len(content) > 16:
        return "possibly hex encoded"
    if len(content) < 50:
        return "short text"

    return "unstructured data"


def _scan_pdf_for_qr(pdf_path: Path) -> InventoryResult:
    """Scan a single PDF for QR codes. Designed for parallel execution."""
    result = InventoryResult(
        pdf_path=str(pdf_path),
        has_qr=False,
        issuer=_extract_issuer_from_filename(pdf_path.name),
    )

    try:
        qr_codes = extract_all_qr_codes(pdf_path)
        result.has_qr = len(qr_codes) > 0

        for qr in qr_codes:
            qr_info = {
                'type': qr.qr_type.value,
                'content': qr.raw_content,
                'page': qr.page_number,
            }

            # Add type-specific info
            if qr.qr_type == QRCodeType.URL:
                try:
                    parsed = urlparse(qr.raw_content.strip())
                    qr_info['domain'] = parsed.netloc
                except Exception:
                    pass
            elif qr.qr_type == QRCodeType.UNKNOWN:
                qr_info['pattern_guess'] = _guess_pattern(qr.raw_content)
            elif qr.qr_type == QRCodeType.PORTUGUESE_INVOICE:
                # Extract field names present
                fields = []
                for part in qr.raw_content.split('*'):
                    if ':' in part:
                        field_code = part.split(':')[0]
                        fields.append(field_code)
                qr_info['fields'] = fields

            result.qr_codes.append(qr_info)

    except Exception as e:
        result.error = str(e)

    return result


def _load_checkpoint(checkpoint_path: Path) -> tuple[set[str], list[InventoryResult]]:
    """Load checkpoint data if exists."""
    if not checkpoint_path.exists():
        return set(), []

    try:
        with open(checkpoint_path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}

        scanned_files = set(data.get('scanned_files', []))
        results = [InventoryResult(**r) for r in data.get('results', [])]
        return scanned_files, results
    except Exception as e:
        logger.warning(f"Failed to load checkpoint: {e}")
        return set(), []


def _save_checkpoint(checkpoint_path: Path, scanned_files: set[str], results: list[InventoryResult]):
    """Save checkpoint data."""
    data = {
        'scanned_files': list(scanned_files),
        'results': [asdict(r) for r in results],
        'checkpoint_time': datetime.now().isoformat(),
    }

    with open(checkpoint_path, 'w', encoding='utf-8') as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


def task_qr_inventory(
    export_path: Path,
    output_path: Optional[Path] = None,
    resume: bool = True,
    max_workers: int = 8,
    checkpoint_interval: int = 100,
):
    """
    Scan all PDFs in export folder and create QR code inventory.

    Args:
        export_path: Path to folder containing PDFs to scan
        output_path: Path to output YAML file (default: config/qr_inventory.yaml)
        resume: Whether to resume from checkpoint if exists
        max_workers: Number of parallel workers for scanning
        checkpoint_interval: Save checkpoint every N files
    """
    console = get_console()

    # Pre-flight check: verify pyzbar is available before starting
    pyzbar_ok, error_msg = check_pyzbar_available()
    if not pyzbar_ok:
        console.error(f"Cannot run qr_inventory: {error_msg}", indent=False)
        raise RuntimeError(f"pyzbar not available: {error_msg}")

    setup_task_logging(export_path, "qr_inventory")

    if output_path is None:
        from papertrail.config import get_current_profile
        profile = get_current_profile()
        if profile and profile.profile_dir:
            output_path = profile.profile_dir / "qr_inventory.yaml"
        else:
            output_path = Path(__file__).parent.parent.parent / "profiles" / "default" / "qr_inventory.yaml"

    checkpoint_path = output_path.with_suffix('.checkpoint.yaml')

    # Find all PDFs
    all_pdfs = list(export_path.rglob("*.pdf"))
    # Filter out merged files
    all_pdfs = [p for p in all_pdfs if not p.name.startswith('merged_')]

    logger.debug(f"Found {len(all_pdfs)} PDFs to scan in {export_path}")

    # Load checkpoint if resuming
    scanned_files: set[str] = set()
    results: list[InventoryResult] = []

    if resume:
        scanned_files, results = _load_checkpoint(checkpoint_path)
        if scanned_files:
            logger.debug(f"Resuming from checkpoint: {len(scanned_files)} already scanned")

    # Filter to unscanned PDFs
    pdfs_to_scan = [p for p in all_pdfs if str(p) not in scanned_files]

    scan_duration = 0.0

    if not pdfs_to_scan:
        logger.debug("All PDFs already scanned")
    else:
        logger.debug(f"Scanning {len(pdfs_to_scan)} PDFs with {max_workers} workers...")

        start_time = datetime.now()

        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_scan_pdf_for_qr, pdf_path): pdf_path
                       for pdf_path in pdfs_to_scan}

            checkpoint_counter = 0

            with console.progress("Scanning PDFs", total=len(futures)) as progress:
                task = progress.add_task("Scanning PDFs", total=len(futures))
                for future in as_completed(futures):
                    pdf_path = futures[future]
                    try:
                        result = future.result()
                        results.append(result)
                        scanned_files.add(str(pdf_path))
                        checkpoint_counter += 1

                        # Save checkpoint periodically
                        if checkpoint_counter >= checkpoint_interval:
                            _save_checkpoint(checkpoint_path, scanned_files, results)
                            checkpoint_counter = 0

                    except Exception as e:
                        logger.error(f"Error scanning {pdf_path}: {e}")
                        results.append(InventoryResult(
                            pdf_path=str(pdf_path),
                            has_qr=False,
                            error=str(e),
                            issuer=_extract_issuer_from_filename(pdf_path.name),
                        ))
                        scanned_files.add(str(pdf_path))

                    progress.update(task, advance=1)

        scan_duration = (datetime.now() - start_time).total_seconds()
        logger.debug(f"Scan completed in {scan_duration:.1f} seconds")

    # Build summary
    logger.debug("Building inventory summary...")

    summary = {
        'total_pdfs': len(results),
        'pdfs_with_qr': sum(1 for r in results if r.has_qr),
        'pdfs_without_qr': sum(1 for r in results if not r.has_qr),
        'scan_errors': sum(1 for r in results if r.error),
    }

    # Count by type
    by_type: dict[str, int] = {}
    for result in results:
        for qr in result.qr_codes:
            qr_type = qr['type']
            by_type[qr_type] = by_type.get(qr_type, 0) + 1

    # Count by issuer
    by_issuer: dict[str, dict[str, int]] = {}
    for result in results:
        issuer = result.issuer or 'unknown'
        if issuer not in by_issuer:
            by_issuer[issuer] = {'with_qr': 0, 'without_qr': 0}
        if result.has_qr:
            by_issuer[issuer]['with_qr'] += 1
        else:
            by_issuer[issuer]['without_qr'] += 1

    # Sort by total documents
    by_issuer = dict(sorted(
        by_issuer.items(),
        key=lambda x: x[1]['with_qr'] + x[1]['without_qr'],
        reverse=True
    ))

    # Collect samples
    samples: dict[str, list[dict]] = {}
    seen_contents: dict[str, set[str]] = {}  # Track unique content per type

    for result in results:
        for qr in result.qr_codes:
            qr_type = qr['type']
            content = qr['content']

            if qr_type not in samples:
                samples[qr_type] = []
                seen_contents[qr_type] = set()

            # Only add unique samples
            content_key = content[:100]  # Use first 100 chars as key
            if content_key not in seen_contents[qr_type] and len(samples[qr_type]) < MAX_SAMPLES_PER_TYPE:
                seen_contents[qr_type].add(content_key)

                sample = {
                    'content': content[:500],  # Truncate long content
                    'file': Path(result.pdf_path).name,
                    'page': qr['page'],
                }

                if qr_type == 'url' and 'domain' in qr:
                    sample['domain'] = qr['domain']
                elif qr_type == 'unknown' and 'pattern_guess' in qr:
                    sample['pattern_guess'] = qr['pattern_guess']
                elif qr_type == 'portuguese_invoice' and 'fields' in qr:
                    # Show which fields are extracted
                    sample['fields_present'] = qr['fields']
                    sample['fields_extracted'] = ['date_issued', 'document_type', 'total_amount', 'issuer_tax_number', 'atcud']

                samples[qr_type].append(sample)

    # Collect URL domain statistics
    url_domains: dict[str, int] = {}
    for result in results:
        for qr in result.qr_codes:
            if qr['type'] == 'url' and 'domain' in qr:
                domain = qr['domain']
                url_domains[domain] = url_domains.get(domain, 0) + 1

    url_domains = dict(sorted(url_domains.items(), key=lambda x: x[1], reverse=True))

    # Build final output
    inventory = {
        'scan_date': datetime.now().isoformat(),
        'scan_duration_seconds': scan_duration if 'scan_duration' in dir() else None,
        'export_path': str(export_path),
        'summary': summary,
        'by_type': by_type,
        'by_issuer': by_issuer,
        'url_domains': url_domains,
        'samples': samples,
    }

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        yaml.dump(inventory, f, default_flow_style=False, allow_unicode=True, width=120)

    logger.debug(f"Inventory written to {output_path}")

    # Clean up checkpoint
    if checkpoint_path.exists():
        checkpoint_path.unlink()
        logger.debug("Checkpoint file removed")

    # Console summary
    qr_pct = 100 * summary['pdfs_with_qr'] / max(1, summary['total_pdfs'])
    console.success(
        f"{summary['total_pdfs']} PDFs scanned, "
        f"{summary['pdfs_with_qr']} with QR codes ({qr_pct:.1f}%)",
        indent=False
    )

    if summary['scan_errors']:
        console.warning(f"{summary['scan_errors']} scan errors", indent=False)

    # Log detailed info to file
    logger.debug("=" * 60)
    logger.debug("QR INVENTORY SUMMARY")
    logger.debug("=" * 60)
    logger.debug(f"Total PDFs scanned: {summary['total_pdfs']}")
    logger.debug(f"PDFs with QR codes: {summary['pdfs_with_qr']} ({qr_pct:.1f}%)")
    logger.debug(f"PDFs without QR codes: {summary['pdfs_without_qr']}")
    if summary['scan_errors']:
        logger.debug(f"Scan errors: {summary['scan_errors']}")

    logger.debug("\nBy QR type:")
    for qr_type, count in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
        logger.debug(f"  {qr_type}: {count}")

    if url_domains:
        logger.debug("\nTop URL domains:")
        for domain, count in list(url_domains.items())[:10]:
            logger.debug(f"  {domain}: {count}")

    logger.debug("\nTop issuers with QR codes:")
    for issuer, stats in list(by_issuer.items())[:10]:
        if stats['with_qr'] > 0:
            logger.debug(f"  {issuer}: {stats['with_qr']} with QR, {stats['without_qr']} without")

    return inventory
