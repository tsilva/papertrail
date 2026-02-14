"""Pipeline task."""

import os
import re
import shutil
import sys
import time
from pathlib import Path

from archive_extractor import extract_archives

from papertrail.config import get_current_profile
from papertrail.console import get_console
import io
import warnings
from contextlib import redirect_stdout, redirect_stderr

from papertrail.logging_utils import get_logger, setup_task_logging, suppress_console_logging
from papertrail.mbox import extract_mbox_attachments
from papertrail.metadata import load_json_data
from papertrail.tasks.validation import validate_merged_pdf

logger = get_logger('cli')


def pipeline(months=2, export_date_arg=None, processed_path_override=None):
    """Run the full document processing pipeline."""
    from papertrail.config import get_passwords

    console = get_console()
    start_time = time.time()

    profile = get_current_profile()
    if not profile:
        console.error("No profile is active.", indent=False)
        sys.exit(1)

    raw_dirs = profile.paths.raw
    PROCESSED_FILES_DIR = profile.paths.processed
    EXPORT_FILES_DIR = profile.paths.export

    missing = []
    if not raw_dirs:
        missing.append("paths.raw")
    if not PROCESSED_FILES_DIR:
        missing.append("paths.processed")
    if not EXPORT_FILES_DIR:
        missing.append("paths.export")
    if missing:
        console.error(f"Missing required profile settings: {', '.join(missing)}", indent=False)
        sys.exit(1)

    log_file_path = setup_task_logging(Path(PROCESSED_FILES_DIR), "pipeline")
    logger.debug("=== PIPELINE STARTED ===")
    logger.debug(f"Log: {log_file_path}")

    # Display pipeline header
    console.pipeline_header(profile.profile.name, str(log_file_path))

    if export_date_arg:
        export_dates = [export_date_arg]
    else:
        from papertrail.dates import compute_month_range
        export_dates = compute_month_range(months)

    for ed in export_dates:
        if not re.match(r"^\d{4}-\d{2}$", ed):
            console.error(f"The export_date must be in YYYY-MM format: {ed}", indent=False)
            sys.exit(1)

    passwords, _ = get_passwords()
    if not passwords:
        logger.debug("No passwords configured. Password-protected archives will be skipped.")

    processed_files_excel_path = Path(PROCESSED_FILES_DIR) / "processed_files.xlsx"

    # Footer accumulators
    warnings: list[str] = []
    summary: dict[str, str] = {}
    output_paths: list[tuple[str, str]] = []

    # ── Stage 1: Ingest raw files ──

    if profile.gmail.enabled:
        with console.step_progress("Download Gmail attachments") as step:
            try:
                from papertrail.gmail import download_gmail_attachments
                from papertrail.dates import month_to_date_range

                raw_path = Path(raw_dirs[0])
                gmail_dir = raw_path / "gmail"

                totals = {
                    "messages_found": 0, "messages_processed": 0, "messages_skipped": 0,
                    "attachments_downloaded": 0, "attachments_failed": 0, "bytes_downloaded": 0,
                }
                for month in export_dates:
                    month_dir = gmail_dir / month
                    month_dir.mkdir(parents=True, exist_ok=True)

                    gmail_start, gmail_end = month_to_date_range([month])
                    logger.debug(f"Gmail {month}: {gmail_start.date()} to {gmail_end.date()}")

                    month_stats = download_gmail_attachments(
                        output_dir=month_dir,
                        start_date=gmail_start,
                        end_date=gmail_end,
                        quiet=True,
                        tracking_dir=gmail_dir,
                    )
                    for key in totals:
                        totals[key] += month_stats[key]

                if totals['attachments_downloaded'] > 0:
                    step.success(
                        f"{totals['messages_processed']} messages, "
                        f"{totals['attachments_downloaded']} new attachments"
                    )
                elif totals['messages_processed'] > 0:
                    step.success(f"{totals['messages_processed']} messages, 0 new attachments")
                else:
                    date_range = f"{export_dates[0]} to {export_dates[-1]}"
                    step.warning(f"No messages found ({date_range})")
            except Exception as e:
                msg = f"Gmail download failed, continuing pipeline ({e})"
                step.warning(msg)
                warnings.append(msg)
                logger.warning(f"Gmail download failed (non-fatal): {e}")

    for rd in raw_dirs:
        # Mbox extraction
        with console.step_progress("Extract mbox attachments") as step:
            logger.debug("### Extract mbox attachments...")
            stats = extract_mbox_attachments(rd)
            if stats['mbox_files'] > 0:
                step.success(f"{stats['mbox_files']} mbox file(s), {stats['attachments_extracted']} attachment(s)")
                logger.debug(f"Processed {stats['mbox_files']} mbox file(s), extracted {stats['attachments_extracted']} attachment(s)")
            else:
                msg = "No mbox files found"
                step.warning(msg)
                warnings.append(msg)
            if stats['errors']:
                step.error(f"{len(stats['errors'])} error(s)")
                logger.error(f"Extract mbox attachments encountered {len(stats['errors'])} error(s)")
                sys.exit(1)
            logger.debug("### Extract mbox attachments... Finished.")

        # Archive extraction
        with console.step_progress("Extract compressed archives") as step:
            logger.debug("### Extract compressed archives...")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), warnings.catch_warnings():
                warnings.simplefilter("ignore")
                results = extract_archives(rd, passwords=passwords if passwords else None)
            total_extracted = 0
            failures = 0
            for archive_path, count in results.items():
                if count == -1:
                    failures += 1
                    logger.debug(f"Failed to extract: {archive_path}")
                else:
                    total_extracted += count
                    logger.debug(f"Extracted {count} files from {archive_path}")
            if total_extracted > 0:
                step.success(f"{total_extracted} files from {len(results) - failures} archive(s)")
            elif failures > 0:
                step.warning(f"{failures} archive(s) failed")
            else:
                step.warning("No archives found")
            logger.debug("### Extract compressed archives... Finished.")

    # ── Stage 2: Extract new documents ──

    from papertrail.tasks.extraction import task_extract_new

    console.step("Classify new documents")
    try:
        extract_stats = task_extract_new(
            Path(PROCESSED_FILES_DIR), [Path(d) for d in raw_dirs], quiet=False,
        )
        if extract_stats is None:
            console.warning("Extraction locked by another process")
        elif extract_stats["failed"] > 0:
            console.error(f"OpenRouter API error ({extract_stats['failed']} failed)")
    except Exception as e:
        console.error(str(e))
        sys.exit(1)

    # Notable items below extraction step
    if extract_stats and extract_stats.get("bundles_split", 0) > 0:
        console.notable(
            f"Split {extract_stats['bundles_split']} PDF bundles into {extract_stats['split_pages']} pages"
        )
    if extract_stats:
        if extract_stats.get("new_issuers"):
            console.notable(f"New issuers: {', '.join(extract_stats['new_issuers'])}")
        unknown_parts = []
        if extract_stats.get("unknown_document_type"):
            unknown_parts.append(f"{extract_stats['unknown_document_type']} document_type")
        if extract_stats.get("unknown_issuing_party"):
            unknown_parts.append(f"{extract_stats['unknown_issuing_party']} issuing_party")
        if unknown_parts:
            unknown_msg = f"Unknown: {', '.join(unknown_parts)}"
            console.notable(unknown_msg)
            warnings.append(f"{unknown_msg} in extracted documents")

        # Update summary
        if extract_stats["new"] > 0 or extract_stats["duplicates"] > 0:
            classified_parts = [f"{extract_stats['new']} new", f"{extract_stats['duplicates']} duplicates"]
            if extract_stats.get("batch_duplicates", 0) > 0:
                classified_parts.append(f"{extract_stats['batch_duplicates']} batch dupes")
            summary["Classified"] = ", ".join(classified_parts)

    # ── Stage 3: Sync orphans ──

    from papertrail.tasks.extraction import task_sync

    console.step("Sync orphaned metadata")
    try:
        sync_stats = task_sync(Path(PROCESSED_FILES_DIR), quiet=False)
        orphans = sync_stats.get("targets", 0)
        if orphans == 0:
            console.success("0 orphans found")
        else:
            resynced = sync_stats.get("new", 0) + sync_stats.get("changed", 0)
            console.success(f"{resynced} orphans re-synced")
    except Exception as e:
        console.error(str(e))
        sys.exit(1)

    # ── Stage 4: Rename files ──

    from papertrail.tasks.organization import task_rename_files

    with console.step_progress("Sync filenames to metadata") as step:
        try:
            rename_stats = task_rename_files(Path(PROCESSED_FILES_DIR), quiet=True)
            step.success(f"{rename_stats['validated']} validated, {rename_stats['renamed']} renamed")
            if rename_stats['renamed'] > 0:
                summary["Renamed"] = f"{rename_stats['renamed']} files"
            orphans = rename_stats.get('orphans', 0)
            if orphans > 0:
                msg = f"{orphans} orphaned JSON sidecar(s) (companion file missing)"
                console.notable(msg)
                warnings.append(msg)
        except Exception as e:
            step.error(str(e))
            sys.exit(1)

    # ── Stage 5: Export to Excel ──

    from papertrail.tasks.export import export_metadata_to_excel

    with console.step_progress("Export to Excel") as step:
        try:
            excel_stats = export_metadata_to_excel(
                Path(PROCESSED_FILES_DIR), str(processed_files_excel_path), quiet=True
            )
            if excel_stats['exported']:
                step.success(f"{excel_stats['exported']} entries")
                summary["Exported"] = f"{excel_stats['exported']} entries"
            else:
                step.warning("No valid metadata found to export")
        except Exception as e:
            step.error(str(e))
            sys.exit(1)

    from papertrail.tasks.organization import copy_matching_files

    export_file_config = profile.export

    profile_context = None
    if profile.profile.tax_number:
        profile_context = {"tax_number": profile.profile.tax_number}

    # ── Stage 6-8: Export, Reconcile, Merge per export month ──

    from papertrail.tasks.reconciliation import _discover_bank_statements, _reconcile_single
    from papertrail.tasks.merge_attachments import merge_reconciled_attachments

    recon_stats_all: list[dict] = []
    merge_rules = export_file_config.merge_rules

    for export_date in export_dates:
        export_date_dir = os.path.join(EXPORT_FILES_DIR, export_date)

        if os.path.exists(export_date_dir):
            shutil.rmtree(export_date_dir)

        # ── Stage 6: Export documents ──

        with console.step_progress(f"Export documents ({export_date})") as step:
            try:
                copy_stats = copy_matching_files(
                    Path(PROCESSED_FILES_DIR),
                    export_date,
                    Path(export_date_dir),
                    export_config=export_file_config,
                    profile_context=profile_context,
                    quiet=True,
                )
                copied = copy_stats.get('copied', 0)
                deduped = copy_stats.get('deduped', 0)
                if copied:
                    msg = f"{copied} files"
                    if deduped > 0:
                        msg += f" ({deduped} content dupes skipped)"
                    step.success(msg)
                else:
                    step.success("0 files")
            except Exception as e:
                step.error(str(e))
                sys.exit(1)

        # ── Stage 7: Reconcile bank statements ──

        all_recon_matches = []
        bank_statements = _discover_bank_statements(Path(export_date_dir))
        for bs_path in bank_statements:
            bs_json = bs_path.with_suffix(".json")
            bs_info = {}
            if bs_json.exists():
                try:
                    bs_data = load_json_data(bs_json)
                    bs_info = bs_data.get("bank_statement", {}) or {}
                except Exception:
                    pass
            account = bs_info.get("account_number", bs_path.stem)
            period = bs_info.get("period_start", export_date)
            if period and len(period) >= 7:
                period = period[:7]

            with console.step_progress(f"Match bank transactions: {account} ({period})") as step:
                try:
                    recon_stats = _reconcile_single(
                        Path(export_date_dir), bs_path, dry_run=False,
                        console=console, quiet=True,
                    )
                    recon_stats_all.append(recon_stats)
                    all_recon_matches.extend(recon_stats.get("matches", []))
                    reconciled = recon_stats["reconciled"]
                    total = recon_stats["total"]
                    pct = recon_stats["reconciliation_rate"]
                    if total > 0:
                        step.success(f"{reconciled}/{total} reconciled ({pct:.0f}%)")
                    else:
                        step.warning("No transactions found")
                except Exception as e:
                    step.warning(f"Reconciliation failed: {e}")
                    logger.warning(f"Reconciliation failed for {bs_path.name}: {e}")

        # ── Stage 7b: Merge attachments (e.g., bank-note → at-irs) ──

        if merge_rules and all_recon_matches:
            with console.step_progress(f"Merge attachments ({export_date})") as step:
                try:
                    merge_stats = merge_reconciled_attachments(
                        Path(export_date_dir), all_recon_matches, merge_rules,
                    )
                    merged = merge_stats["merged"]
                    errors = merge_stats["errors"]
                    if merged > 0:
                        step.success(f"{merged} document(s) merged")
                    elif errors > 0:
                        step.warning(f"{errors} merge error(s)")
                    else:
                        step.success("No merges needed")
                except Exception as e:
                    step.warning(f"Merge attachments failed: {e}")
                    logger.warning(f"Merge attachments failed: {e}")

        # ── Stage 8: Merge all PDFs (final combined files) ──

        with console.step_progress(f"Merge PDFs ({export_date})") as step:
            logger.debug("### Merge PDFs...")
            try:
                from pdf_gluer import merge_all_pdfs
                with suppress_console_logging():
                    merge_all_pdfs(export_date_dir)

                merged_files = list(Path(export_date_dir).glob("merged_*.pdf"))
                if merged_files:
                    prefixes = sorted({
                        f.stem.split("_", 1)[1].upper() + "_"
                        for f in merged_files if "_" in f.stem
                    })
                    step.success(f"{len(merged_files)} merged PDFs ({', '.join(prefixes)})")
                else:
                    step.success("0 merged PDFs")

                logger.debug("### Merge PDFs... Finished.")
            except Exception as e:
                step.error(f"PDF merge failed: {e}")
                logger.error(f"Merge PDFs failed: {e}")
                sys.exit(1)

        with suppress_console_logging():
            validate_merged_pdf(Path(export_date_dir))

        output_paths.append(("Export", str(export_date_dir)))

    # Build reconciliation summary
    if recon_stats_all:
        total_statements = len(recon_stats_all)
        total_reconciled = sum(s["reconciled"] for s in recon_stats_all)
        total_txns = sum(s["total"] for s in recon_stats_all)
        if total_txns > 0:
            avg_rate = total_reconciled / total_txns * 100
            summary["Reconciled"] = (
                f"{total_statements} statement{'s' if total_statements != 1 else ''}, "
                f"{avg_rate:.0f}% reconciled ({total_reconciled}/{total_txns})"
            )

    # Add output paths
    output_paths.append(("Excel", str(processed_files_excel_path)))
    output_paths.append(("Log", str(log_file_path)))

    # Show pipeline footer
    elapsed = time.time() - start_time
    console.pipeline_footer(
        elapsed_seconds=elapsed,
        warnings=warnings if warnings else None,
        summary=summary if summary else None,
        output_paths=output_paths,
    )
    logger.debug("All steps completed successfully.")
