"""Canonical command layer for papertrail."""

from __future__ import annotations

import fcntl
import hashlib
import io
import os
import re
import shutil
import sys
import tempfile
import time
import warnings
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Optional

import fitz
import pandas as pd

from papertrail.config import get_gmail_config_paths, get_passwords_from_profile
from papertrail.archive_extract import extract_archives
from papertrail.engine import DocumentEngine
from papertrail.gmail import download_gmail_attachments
from papertrail.logging_utils import (
    get_logger,
    setup_failure_logger,
    setup_task_logging,
    suppress_console_logging,
)
from papertrail.mbox import extract_mbox_attachments
from papertrail.models import DocumentMetadata, clean_enum_string
from papertrail.naming import sanitize_filename_component
from papertrail.pdf_merge import merge_all_pdfs
from papertrail.repository import DocumentRepository
from papertrail.rules import RuleEngine
from papertrail.runtime import Runtime
from papertrail.utils import compute_month_range, make_matcher, month_to_date_range

from .check import run_check, validate_merged_pdf
from .reconcile import (
    discover_bank_statements,
    reconcile_single,
)

logger = get_logger("commands")
_PDFPRESS_COMPRESSOR = None


@contextmanager
def _task_log_context(runtime: Runtime, processed_path: Path, task_name: str, *, show_header: bool = True):
    log_file_path = setup_task_logging(processed_path, task_name)
    logger.debug(f"=== {task_name.upper()} STARTED ===")
    logger.debug(f"Log: {log_file_path}")
    if show_header:
        runtime.console.detail(f"Log: {log_file_path}", indent=False)
    yield log_file_path


def _pipeline_step(console, label: str, action):
    with console.step_progress(label) as step:
        try:
            return action(step)
        except Exception as exc:
            step.error(str(exc))
            raise


def _run_export_period(
    runtime: Runtime,
    processed_path: Path,
    export_dir: Path,
    export_date: str,
    *,
    export_file_config,
    profile_context: dict | None,
    merge_rules,
    run_merge_pdfs: bool = False,
) -> tuple[Path, list[dict]]:
    console = runtime.console
    export_date_dir = export_dir / export_date
    if export_date_dir.exists():
        shutil.rmtree(export_date_dir)

    def export_documents(step):
        copy_stats = copy_matching(
            runtime,
            processed_path,
            export_date,
            export_date_dir,
            export_config=export_file_config,
            profile_context=profile_context,
            quiet=True,
        )
        if copy_stats.get("copied", 0):
            message = f"{copy_stats['copied']} files"
            if copy_stats.get("deduped", 0) > 0:
                message += f" ({copy_stats['deduped']} content dupes skipped)"
            step.success(message)
        else:
            step.success("0 files")
        return copy_stats

    _pipeline_step(console, f"Export documents ({export_date})", export_documents)

    repository = DocumentRepository(runtime)
    bank_statements = discover_bank_statements(repository, export_date_dir)
    all_recon_matches = []
    recon_stats_all: list[dict] = []

    for statement_path in bank_statements:
        statement_info = {}
        statement_json = statement_path.with_suffix(".json")
        if statement_json.exists():
            try:
                statement_info = repository.load_metadata(statement_json).get("bank_statement", {}) or {}
            except (OSError, UnicodeDecodeError, ValueError):
                pass
        account = statement_info.get("account_number", statement_path.stem)
        period = statement_info.get("period_start", export_date)
        if period and len(period) >= 7:
            period = period[:7]
        with console.step_progress(f"Match bank transactions: {account} ({period})") as step:
            try:
                recon_stats = reconcile_single(
                    runtime,
                    repository,
                    export_date_dir,
                    statement_path,
                    dry_run=False,
                    quiet=True,
                )
                recon_stats_all.append(recon_stats)
                all_recon_matches.extend(recon_stats.get("matches", []))
                total = recon_stats["total"]
                if total > 0:
                    step.success(
                        f"{recon_stats['reconciled']}/{total} reconciled "
                        f"({recon_stats['reconciliation_rate']:.0f}%)"
                    )
                else:
                    step.warning("No transactions found")
            except Exception as exc:
                step.warning(f"Reconciliation failed: {exc}")
                logger.warning(f"Reconciliation failed for {statement_path.name}: {exc}")

    if merge_rules and all_recon_matches:
        with console.step_progress(f"Merge attachments ({export_date})") as step:
            try:
                merge_stats = merge_reconciled_attachments(
                    runtime,
                    export_date_dir,
                    all_recon_matches,
                    merge_rules,
                )
                if merge_stats["merged"] > 0:
                    step.success(f"{merge_stats['merged']} document(s) merged")
                elif merge_stats["errors"] > 0:
                    step.warning(f"{merge_stats['errors']} merge error(s)")
                else:
                    step.success("No merges needed")
            except Exception as exc:
                step.warning(f"Merge attachments failed: {exc}")
                logger.warning(f"Merge attachments failed: {exc}")

    if run_merge_pdfs:
        def merge_pdfs(step):
            with suppress_console_logging():
                merged_outputs = merge_all_pdfs(str(export_date_dir))
                _compress_exported_pdfs(list(merged_outputs.values()))
            merged_files = list(export_date_dir.glob("merged_*.pdf"))
            if merged_files:
                prefixes = sorted(
                    {
                        file.stem.split("_", 1)[1].upper() + "_"
                        for file in merged_files
                        if "_" in file.stem
                    }
                )
                step.success(f"{len(merged_files)} merged PDFs ({', '.join(prefixes)})")
            else:
                step.success("0 merged PDFs")

        _pipeline_step(console, f"Merge PDFs ({export_date})", merge_pdfs)

        with suppress_console_logging():
            validate_merged_pdf(export_date_dir)

    return export_date_dir, recon_stats_all


def extract(
    runtime: Runtime,
    processed_path: Path,
    raw_paths: list[Path],
    *,
    quiet: bool = False,
) -> dict | None:
    lock_path = runtime.paths.cache / ".extract.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logger.error("Another extract process is already running. Exiting.")
        lock_file.close()
        return None

    try:
        with _task_log_context(runtime, processed_path, "extract_new", show_header=not quiet):
            logs_dir = processed_path / "logs"
            failure_log_path = logs_dir / "classification_failures.log"
            failure_logger = setup_failure_logger(failure_log_path)
            return DocumentEngine(runtime).extract(
                processed_path,
                raw_paths,
                quiet=quiet,
                failure_logger=failure_logger,
            )
    finally:
        fcntl.flock(lock_file, fcntl.LOCK_UN)
        lock_file.close()


def sync(
    runtime: Runtime,
    processed_path: Path,
    *,
    dry_run: bool = False,
    all_unknown: bool = False,
    pattern: str | None = None,
    workers: int = 1,
    all: bool = False,
    quiet: bool = False,
) -> dict:
    with _task_log_context(runtime, processed_path, "sync", show_header=not quiet):
        return DocumentEngine(runtime).sync(
            processed_path,
            dry_run=dry_run,
            all_unknown=all_unknown,
            pattern=pattern,
            workers=workers,
            all=all,
            quiet=quiet,
        )


def rename(runtime: Runtime, processed_path: Path, *, quiet: bool = False) -> dict:
    export_root = runtime.profile.paths.export
    if export_root:
        export_path = Path(export_root).resolve()
        target_path = processed_path.resolve()
        if target_path == export_path or export_path in target_path.parents:
            raise RuntimeError(
                "rename cannot be run on export directories; regenerate exports instead."
            )

    repository = DocumentRepository(runtime)
    with _task_log_context(runtime, processed_path, "rename_files", show_header=not quiet):
        stats = repository.repair_filenames(processed_path)
        if not quiet:
            runtime.console.success(
                f"{stats['validated']} files validated, {stats['renamed']} renamed",
                indent=False,
            )
        logger.debug(f"Renaming complete. Renamed {stats['renamed']} files.")
        return stats


def _build_filename_from_fields(metadata: dict, fields: list[str], file_hash: str, *, profile_context: dict | None = None) -> str:
    engine = RuleEngine(profile_context=profile_context)
    parts = []
    for field_name in fields:
        value = engine.get_nested_value(metadata, field_name)
        if value is not None and str(value).strip():
            component = engine.resolve_profile_value(str(value))
            component = sanitize_filename_component(component.strip())
            if len(component) > 80:
                component = component[:80].rsplit(" ", 1)[0]
            parts.append(component)
    extension = metadata.get("source_extension") or ".pdf"
    parts.append(f"{file_hash}{extension}")
    return " - ".join(parts).lower()


def _should_skip_copy(src: Path, dst: Path) -> bool:
    from papertrail.hashing import hash_file_fast

    return dst.exists() and src.stat().st_size == dst.stat().st_size and hash_file_fast(src) == hash_file_fast(dst)


def _check_file_size(src: Path, max_file_size_mb: float | None) -> None:
    if max_file_size_mb is None:
        return
    size = src.stat().st_size
    threshold = max_file_size_mb * 1024 * 1024
    if size >= threshold:
        size_mb = size / (1024 * 1024)
        logger.warning(f"Large file: {src.name} ({size_mb:.1f} MB exceeds {max_file_size_mb} MB threshold)")


def _get_pdfpress_compressor():
    global _PDFPRESS_COMPRESSOR
    if _PDFPRESS_COMPRESSOR is None:
        try:
            from pdfpress import PDFCompressor
        except ImportError as exc:
            raise RuntimeError(
                "pdfpress is required for export PDF compression. Install the project dependencies again."
            ) from exc
        _PDFPRESS_COMPRESSOR = PDFCompressor(quality="ebook")
    return _PDFPRESS_COMPRESSOR


def _compress_pdf_export(pdf_path: Path) -> None:
    if pdf_path.suffix.lower() != ".pdf" or not pdf_path.exists():
        return

    original_size = pdf_path.stat().st_size
    compressor = _get_pdfpress_compressor()

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_output = Path(tmp_dir) / pdf_path.name
        outcome = compressor.compress(pdf_path, tmp_output)
        shutil.move(tmp_output, pdf_path)

    final_size = pdf_path.stat().st_size
    logger.debug(
        f"[EXPORT-COMPRESS] {pdf_path.name}: {original_size} -> {final_size} bytes "
        f"(strategy={outcome.best_strategy})"
    )


def _compress_exported_pdfs(pdf_paths: list[Path]) -> None:
    for pdf_path in pdf_paths:
        _compress_pdf_export(pdf_path)


def copy_matching(
    runtime: Runtime,
    processed_path: Path,
    pattern: str,
    dest_folder: Path,
    *,
    incremental: bool = False,
    export_config=None,
    profile_context: Optional[dict] = None,
    quiet: bool = False,
) -> dict:
    repository = DocumentRepository(runtime)
    matcher = make_matcher(pattern, use_search=True)
    dest_folder.mkdir(parents=True, exist_ok=True)

    file_mappings = export_config.file_mappings if export_config is not None else None
    max_file_size_mb = export_config.max_file_size_mb if export_config is not None else None
    use_prefixes = file_mappings is not None and file_mappings.enabled
    engine = RuleEngine(profile_context=profile_context)

    stats = {"copied": 0, "skipped": 0, "deduped": 0, "total": 0}
    seen_content_hashes: set[str] = set()

    documents = list(repository.iter_documents(processed_path, validate=False, require_companion=True))
    for json_path, doc_path, metadata in runtime.console.track(documents, "Copying files"):
        metadata_dict = metadata.model_dump() if isinstance(metadata, DocumentMetadata) else metadata
        if not matcher(doc_path.name) and not matcher(json_path.name):
            continue

        stats["total"] += 1
        content_hash = metadata_dict.get("hash_content")
        if content_hash and content_hash in seen_content_hashes:
            stats["deduped"] += 1
            logger.debug(f"[EXPORT-DEDUP] Skipping {doc_path.name} (content hash {content_hash} already exported)")
            continue
        if content_hash:
            seen_content_hashes.add(content_hash)

        if use_prefixes:
            prefix = engine.evaluate_export_prefix(metadata_dict, file_mappings=file_mappings)
            if file_mappings.filename_fields:
                file_hash = metadata_dict.get("hash_file", doc_path.stem.split(" - ")[-1])
                base_name = _build_filename_from_fields(
                    metadata_dict,
                    list(file_mappings.filename_fields),
                    file_hash,
                    profile_context=profile_context,
                )
            else:
                base_name = doc_path.name
            dest_doc = dest_folder / f"{prefix}{base_name}"
            dest_json = dest_doc.with_suffix(".json")
        else:
            dest_doc = dest_folder / doc_path.name
            dest_json = dest_folder / json_path.name

        if incremental and _should_skip_copy(doc_path, dest_doc):
            stats["skipped"] += 1
            continue

        _check_file_size(doc_path, max_file_size_mb)
        shutil.copy2(doc_path, dest_doc)
        if dest_doc.suffix.lower() == ".pdf":
            _compress_pdf_export(dest_doc)
        metadata_copy = dict(metadata_dict)
        metadata_copy["source_filename"] = doc_path.name
        repository.save_json(dest_json, metadata_copy)
        stats["copied"] += 1

    if not quiet:
        runtime.console.success(f"Copied {stats['copied']} files to {dest_folder.name}", indent=False)
    return stats


def export_excel(runtime: Runtime, processed_path: Path, excel_output_path: str, *, quiet: bool = False) -> dict:
    repository = DocumentRepository(runtime)
    metadata_list = []

    for metadata_path, metadata in repository.load_sidecars_parallel(
        processed_path,
        validate=True,
        show_progress=not quiet,
        progress_desc="Collecting metadata",
    ):
        metadata_dict = metadata.model_dump()
        metadata_dict.pop("class_reasoning", None)

        doc_path = repository.find_companion(metadata_path, metadata_dict)
        filename = doc_path.name if doc_path and doc_path.exists() else ""
        metadata_dict["filename"] = filename
        metadata_dict["filename_length"] = len(filename)

        try:
            date_parts = metadata.date_issued.split("-")
            metadata_dict["year"] = int(date_parts[0])
            metadata_dict["month"] = int(date_parts[1])
        except (IndexError, ValueError, AttributeError):
            metadata_dict["year"] = None
            metadata_dict["month"] = None

        if isinstance(metadata_dict.get("document_type"), str):
            metadata_dict["document_type"] = clean_enum_string(metadata_dict["document_type"], "DocumentType")

        metadata_list.append(metadata_dict)

    if not metadata_list:
        if not quiet:
            runtime.console.warning("No valid metadata found to export", indent=False)
        return {"exported": 0}

    dataframe = pd.DataFrame(metadata_list)
    ordered_cols = [
        "class_confidence",
        "date_issued",
        "year",
        "month",
        "hash_content",
        "hash_file",
        "filename",
        "filename_length",
        "page_count",
        "document_type",
        "document_type_raw",
        "document_title",
        "issuing_party",
        "issuing_party_raw",
        "total_amount",
        "total_amount_currency",
    ]
    extra_cols = [col for col in dataframe.columns if col not in ordered_cols]
    dataframe = dataframe[ordered_cols + extra_cols]

    if "date_issued" in dataframe.columns:
        dataframe = dataframe.sort_values(by="date_issued", ascending=False)

    with pd.ExcelWriter(excel_output_path, engine="openpyxl") as writer:
        dataframe.to_excel(writer, index=False, sheet_name="Sheet1")
        worksheet = writer.sheets["Sheet1"]
        worksheet.freeze_panes = "A2"

        from openpyxl.utils import get_column_letter

        for col in ordered_cols:
            if col in dataframe.columns:
                col_idx = dataframe.columns.get_loc(col) + 1
                col_letter = get_column_letter(col_idx)
                value_lengths = [len(str(value)) for value in dataframe[col].values if value is not None]
                max_len = max(value_lengths + [len(col)])
                worksheet.column_dimensions[col_letter].width = min(max_len + 2, 102)

        for col in ("year", "month", "filename_length"):
            if col in dataframe.columns:
                col_letter = get_column_letter(dataframe.columns.get_loc(col) + 1)
                worksheet.column_dimensions[col_letter].hidden = True

    if not quiet:
        runtime.console.success(f"Exported {len(dataframe)} entries", indent=False)
    logger.debug(f"Exported {len(dataframe)} entries to {excel_output_path}")
    return {"exported": len(dataframe)}


def _calculate_directory_hash(directory: Path) -> str:
    from papertrail.hashing import hash_file_fast

    pdf_files = sorted(directory.glob("*.pdf"))
    if not pdf_files:
        return ""
    combined = [f"{path.name}:{hash_file_fast(path)}" for path in pdf_files]
    return hashlib.sha256("\n".join(combined).encode()).hexdigest()[:16]


def _directory_has_changed(directory: Path) -> bool:
    hash_file_path = directory / ".directory_hash"
    current_hash = _calculate_directory_hash(directory)
    if not current_hash:
        return False
    if not hash_file_path.exists():
        hash_file_path.write_text(current_hash, encoding="utf-8")
        return True
    stored_hash = hash_file_path.read_text(encoding="utf-8").strip()
    if current_hash != stored_hash:
        hash_file_path.write_text(current_hash, encoding="utf-8")
        return True
    return False


def export_dates(
    runtime: Runtime,
    processed_path: Path,
    export_base_dir: Path,
    run_merge: bool = False,
    *,
    export_config=None,
    profile_context: dict | None = None,
) -> None:
    repository = DocumentRepository(runtime)

    with _task_log_context(runtime, processed_path, "export_all_dates", show_header=False):
        all_dates = repository.unique_dates(processed_path)
        if not all_dates:
            runtime.console.warning("No dates found in processed files", indent=False)
            return

        total_copied = 0
        total_skipped = 0
        changed_directories = []
        for date in runtime.console.track(all_dates, "Exporting dates"):
            export_date_dir = export_base_dir / date
            if export_date_dir.exists():
                shutil.rmtree(export_date_dir)

            stats = copy_matching(
                runtime,
                processed_path,
                date,
                export_date_dir,
                incremental=False,
                export_config=export_config,
                profile_context=profile_context,
                quiet=True,
            )
            total_copied += stats["copied"]
            total_skipped += stats["skipped"]

            if stats["copied"] > 0:
                changed_directories.append(export_date_dir)
            elif stats["total"] > 0 and export_date_dir.exists() and _directory_has_changed(export_date_dir):
                changed_directories.append(export_date_dir)

        runtime.console.success(f"{len(all_dates)} dates exported, {total_copied} files copied", indent=False)
        logger.debug(
            f"Processed {len(all_dates)} date(s), Total files copied: {total_copied}, Skipped: {total_skipped}"
        )

        if run_merge and changed_directories:
            for export_dir in runtime.console.track(changed_directories, "Merging PDFs"):
                try:
                    merged_outputs = merge_all_pdfs(str(export_dir))
                    _compress_exported_pdfs(list(merged_outputs.values()))
                    validate_merged_pdf(export_dir)
                except Exception as exc:
                    logger.error(f"Merge failed: {exc}")

            runtime.console.success(f"Merged {len(changed_directories)} directories", indent=False)


def archive(runtime: Runtime, processed_path: Path, digests: list[str], *, dry_run: bool = False) -> None:
    repository = DocumentRepository(runtime)
    if dry_run:
        runtime.console.info("Dry run - no files will be moved", indent=False)
    stats = repository.archive_by_hash_file(digests, dry_run=dry_run, scope=processed_path)
    for digest in stats["not_found"]:
        runtime.console.warning(f"[NOT FOUND] {digest}", indent=False)

    runtime.console.info(
        f"Archive: {stats['found']} found, {stats['archived']} archived, {len(stats['not_found'])} not found",
        indent=False,
    )
    if not dry_run and stats["archived"] > 0:
        runtime.console.detail(f"Archived to: {stats['archive_dir']}", indent=False)


def gmail(runtime: Runtime, *, months: int = 2) -> None:
    raw_paths = runtime.profile.paths.raw
    processed_path_str = runtime.profile.paths.processed

    if processed_path_str:
        setup_task_logging(Path(processed_path_str), "gmail_download")

    if not raw_paths or not processed_path_str:
        missing = []
        if not raw_paths:
            missing.append("paths.raw")
        if not processed_path_str:
            missing.append("paths.processed")
        runtime.console.error(f"Missing required profile settings: {', '.join(missing)}", indent=False)
        raise RuntimeError(f"Missing required profile settings: {', '.join(missing)}")

    raw_path = Path(raw_paths[0])
    export_dates_list = compute_month_range(months)
    totals = {
        "messages_found": 0,
        "messages_processed": 0,
        "messages_skipped": 0,
        "attachments_downloaded": 0,
        "attachments_failed": 0,
        "bytes_downloaded": 0,
    }
    gmail_dir = raw_path / "gmail"
    tracking_dir = Path(processed_path_str) / "logs" / "gmail_tracking"
    tracking_dir.mkdir(parents=True, exist_ok=True)
    paths = get_gmail_config_paths(runtime.profile)

    for month in export_dates_list:
        month_dir = gmail_dir / month
        month_dir.mkdir(parents=True, exist_ok=True)
        start_date, end_date = month_to_date_range([month])
        logger.debug(f"Gmail {month}: {start_date.date()} to {end_date.date()} -> {month_dir}")

        try:
            stats = download_gmail_attachments(
                output_dir=month_dir,
                start_date=start_date,
                end_date=end_date,
                tracking_dir=tracking_dir,
                credentials_path=paths["credentials"],
                token_path=paths["token"],
                settings_path=paths["settings"],
                settings=runtime.profile.gmail,
                console=runtime.console,
            )
        except FileNotFoundError as exc:
            runtime.console.error(f"Gmail credentials not found: {exc}", indent=False)
            raise RuntimeError(f"Gmail credentials not found: {exc}") from exc
        except Exception as exc:
            error_type = type(exc).__name__
            runtime.console.error(f"Gmail download failed ({error_type}): {exc}", indent=False)
            raise RuntimeError(f"Gmail download failed ({error_type}): {exc}") from exc

        for key in totals:
            totals[key] += stats[key]

    if totals["attachments_downloaded"] > 0:
        runtime.console.success(
            f"{totals['messages_processed']} messages processed, {totals['attachments_downloaded']} new attachments",
            indent=False,
        )
    elif totals["messages_processed"] > 0:
        runtime.console.success(f"{totals['messages_processed']} messages processed, 0 new attachments", indent=False)
    else:
        date_range = f"{export_dates_list[0]} to {export_dates_list[-1]}"
        runtime.console.warning(f"No messages found ({date_range})", indent=False)


def check(runtime: Runtime, processed_path: Path, *, verify_hashes: bool = False, dry_run: bool = False) -> None:
    repository = DocumentRepository(runtime)
    engine = DocumentEngine(runtime, repository)
    run_check(runtime, repository, engine, processed_path, verify_hashes=verify_hashes, dry_run=dry_run)


def _page_signature(page: fitz.Page) -> str:
    text = page.get_text("text").strip()
    if text:
        payload = text.encode("utf-8")
    else:
        pix = page.get_pixmap(matrix=fitz.Matrix(0.5, 0.5), colorspace=fitz.csGRAY, alpha=False)
        payload = f"{pix.width}x{pix.height}:".encode("ascii") + bytes(pix.samples)
    return hashlib.sha256(payload).hexdigest()


def _pdf_tail_matches_attachment(target_pdf: Path, attach_pdf: Path) -> bool:
    with fitz.open(target_pdf) as target_doc, fitz.open(attach_pdf) as attach_doc:
        attach_page_count = len(attach_doc)
        if attach_page_count == 0 or len(target_doc) < attach_page_count:
            return False
        start_index = len(target_doc) - attach_page_count
        for offset in range(attach_page_count):
            if _page_signature(target_doc[start_index + offset]) != _page_signature(attach_doc[offset]):
                return False
    return True


def merge_reconciled_attachments(runtime: Runtime, export_path: Path, all_matches: list, merge_rules: list) -> dict:
    stats = {"merged": 0, "skipped": 0, "errors": 0}
    if not merge_rules or not all_matches:
        return stats

    merged_attachments: set[str] = set()
    modified_targets: set[Path] = set()
    engine = RuleEngine()

    for match in all_matches:
        for target, attachment in engine.select_merge_pairs(match, merge_rules):
            target_pdf = export_path / target.pdf_filename
            if not target_pdf.exists() or target_pdf.suffix.lower() != ".pdf":
                continue

            if attachment.pdf_filename in merged_attachments:
                stats["skipped"] += 1
                continue

            attach_pdf = export_path / attachment.pdf_filename
            if not attach_pdf.exists() or attach_pdf.suffix.lower() != ".pdf":
                continue
            if target_pdf == attach_pdf:
                continue
            if _pdf_tail_matches_attachment(target_pdf, attach_pdf):
                merged_attachments.add(attachment.pdf_filename)
                stats["skipped"] += 1
                continue

            try:
                import pikepdf

                with pikepdf.open(target_pdf, allow_overwriting_input=True) as target_doc:
                    with pikepdf.open(attach_pdf) as attach_doc:
                        target_doc.pages.extend(attach_doc.pages)
                    target_doc.save(target_pdf)
                merged_attachments.add(attachment.pdf_filename)
                modified_targets.add(target_pdf)
                stats["merged"] += 1
            except Exception as exc:
                stats["errors"] += 1
                logger.error(
                    f"[MERGE] Failed to append {attachment.pdf_filename} to {target.pdf_filename}: {exc}"
                )

    _compress_exported_pdfs(sorted(modified_targets))

    return stats


def reconcile(
    runtime: Runtime,
    export_path: Path,
    *,
    excel_path: Optional[Path] = None,
    dry_run: bool = False,
) -> None:
    repository = DocumentRepository(runtime)
    merge_rules = runtime.profile.export.merge_rules
    with _task_log_context(runtime, export_path, "reconcile"):
        if excel_path is not None:
            excel_paths = [excel_path]
        else:
            excel_paths = discover_bank_statements(repository, export_path)

        if not excel_paths:
            runtime.console.warning("No bank statements found to reconcile", indent=False)
            return

        all_recon_matches: list[dict] = []
        for path in excel_paths:
            if not path.exists():
                runtime.console.error(f"Excel file not found: {path}", indent=False)
                continue
            with runtime.console.task(f"Reconcile: {path.name}"):
                stats = reconcile_single(runtime, repository, export_path, path, dry_run=dry_run)
                all_recon_matches.extend(stats.get("matches", []))

        if not dry_run and merge_rules and all_recon_matches:
            merge_stats = merge_reconciled_attachments(runtime, export_path, all_recon_matches, merge_rules)
            if merge_stats["merged"] > 0:
                runtime.console.success(
                    f"Merged attachments into {merge_stats['merged']} document(s)",
                    indent=False,
                )
            elif merge_stats["errors"] > 0:
                runtime.console.warning(
                    f"Attachment merge completed with {merge_stats['errors']} error(s)",
                    indent=False,
                )


def review(runtime: Runtime, export_path: Path) -> None:
    os.environ["PAPERTRAIL_PROFILE"] = runtime.profile_name
    from tools.shared import launch_tool
    launch_tool("review")


def pipeline(
    runtime: Runtime,
    *,
    months: int = 2,
    export_date_arg: Optional[str] = None,
    processed_path_override: Optional[Path] = None,
) -> None:
    console = runtime.console
    start_time = time.time()
    profile = runtime.profile

    raw_dirs = profile.paths.raw
    processed_dir = str(processed_path_override or profile.paths.processed or "")
    export_dir = profile.paths.export

    missing = []
    if not raw_dirs:
        missing.append("paths.raw")
    if not processed_dir:
        missing.append("paths.processed")
    if not export_dir:
        missing.append("paths.export")
    if missing:
        console.error(f"Missing required profile settings: {', '.join(missing)}", indent=False)
        sys.exit(1)

    processed_path = Path(processed_dir)
    log_file_path = setup_task_logging(processed_path, "pipeline")
    console.pipeline_header(profile.profile.name, str(log_file_path))

    export_dates_list = [export_date_arg] if export_date_arg else compute_month_range(months)
    for export_date in export_dates_list:
        if not re.match(r"^\d{4}-\d{2}$", export_date):
            console.error(f"The export_date must be in YYYY-MM format: {export_date}", indent=False)
            sys.exit(1)

    passwords, _ = get_passwords_from_profile(profile)
    if not passwords:
        logger.debug("No passwords configured. Password-protected archives will be skipped.")

    processed_files_excel_path = processed_path / "processed_files.xlsx"
    pipeline_warnings: list[str] = []
    summary: dict[str, str] = {}
    output_paths: list[tuple[str, str]] = []

    if profile.gmail.enabled:
        with console.step_progress("Download Gmail attachments") as step:
            try:
                gmail(runtime, months=months)
                step.success("Download completed")
            except Exception as exc:
                msg = f"Gmail download failed, continuing pipeline ({exc})"
                step.warning(msg)
                pipeline_warnings.append(msg)
                logger.warning(f"Gmail download failed (non-fatal): {exc}")

    for raw_dir in raw_dirs:
        with console.step_progress("Extract mbox attachments") as step:
            stats = extract_mbox_attachments(raw_dir)
            if stats["mbox_files"] > 0:
                step.success(f"{stats['mbox_files']} mbox file(s), {stats['attachments_extracted']} attachment(s)")
            else:
                step.warning("No mbox files found")
            if stats["errors"]:
                step.error(f"{len(stats['errors'])} error(s)")
                sys.exit(1)

        with console.step_progress("Extract compressed archives") as step:
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), warnings.catch_warnings():
                warnings.simplefilter("ignore")
                results = extract_archives(raw_dir, passwords=passwords if passwords else None)
            total_extracted = 0
            failures = 0
            for count in results.values():
                if count == -1:
                    failures += 1
                else:
                    total_extracted += count
            if total_extracted > 0:
                step.success(f"{total_extracted} files from {len(results) - failures} archive(s)")
            elif failures > 0:
                step.warning(f"{failures} archive(s) failed")
            else:
                step.warning("No archives found")

    console.step("Classify new documents")
    try:
        extract_stats = extract(runtime, processed_path, [Path(directory) for directory in raw_dirs], quiet=False)
        if extract_stats is None:
            console.warning("Extraction locked by another process")
        elif extract_stats["failed"] > 0:
            console.error(f"OpenRouter API error ({extract_stats['failed']} failed)")
    except Exception as exc:
        console.error(str(exc))
        sys.exit(1)

    if extract_stats and extract_stats.get("bundles_split", 0) > 0:
        console.notable(f"Split {extract_stats['bundles_split']} PDF bundles into {extract_stats['split_pages']} pages")
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
            pipeline_warnings.append(f"{unknown_msg} in extracted documents")

        if extract_stats["new"] > 0 or extract_stats["duplicates"] > 0:
            classified_parts = [f"{extract_stats['new']} new", f"{extract_stats['duplicates']} duplicates"]
            if extract_stats.get("batch_duplicates", 0) > 0:
                classified_parts.append(f"{extract_stats['batch_duplicates']} batch dupes")
            summary["Classified"] = ", ".join(classified_parts)

    console.step("Sync orphaned metadata")
    try:
        sync_stats = sync(runtime, processed_path, quiet=False)
        orphans = sync_stats.get("targets", 0)
        if orphans == 0:
            console.success("0 orphans found")
        else:
            resynced = sync_stats.get("new", 0) + sync_stats.get("changed", 0)
            console.success(f"{resynced} orphans re-synced")
    except Exception as exc:
        console.error(str(exc))
        sys.exit(1)

    with console.step_progress("Sync filenames to metadata") as step:
        try:
            rename_stats = rename(runtime, processed_path, quiet=True)
            step.success(f"{rename_stats['validated']} validated, {rename_stats['renamed']} renamed")
            if rename_stats["renamed"] > 0:
                summary["Renamed"] = f"{rename_stats['renamed']} files"
            orphans = rename_stats.get("orphans", 0)
            if orphans > 0:
                msg = f"{orphans} orphaned JSON sidecar(s) (companion file missing)"
                console.notable(msg)
                pipeline_warnings.append(msg)
        except Exception as exc:
            step.error(str(exc))
            sys.exit(1)

    with console.step_progress("Export to Excel") as step:
        try:
            excel_stats = export_excel(runtime, processed_path, str(processed_files_excel_path), quiet=True)
            if excel_stats["exported"]:
                step.success(f"{excel_stats['exported']} entries")
                summary["Exported"] = f"{excel_stats['exported']} entries"
            else:
                step.warning("No valid metadata found to export")
        except Exception as exc:
            step.error(str(exc))
            sys.exit(1)

    export_file_config = profile.export
    profile_context = {"tax_number": profile.profile.tax_number} if profile.profile.tax_number else None
    recon_stats_all: list[dict] = []
    merge_rules = export_file_config.merge_rules

    for export_date in export_dates_list:
        try:
            export_date_dir, period_recon_stats = _run_export_period(
                runtime,
                processed_path,
                Path(export_dir),
                export_date,
                export_file_config=export_file_config,
                profile_context=profile_context,
                merge_rules=merge_rules,
                run_merge_pdfs=False,
            )
        except Exception as exc:
            logger.error(f"Export pipeline failed for {export_date}: {exc}")
            sys.exit(1)

        recon_stats_all.extend(period_recon_stats)
        output_paths.append(("Export", str(export_date_dir)))

    if recon_stats_all:
        total_statements = len(recon_stats_all)
        total_reconciled = sum(stats["reconciled"] for stats in recon_stats_all)
        total_txns = sum(stats["total"] for stats in recon_stats_all)
        if total_txns > 0:
            avg_rate = total_reconciled / total_txns * 100
            summary["Reconciled"] = (
                f"{total_statements} statement{'s' if total_statements != 1 else ''}, "
                f"{avg_rate:.0f}% reconciled ({total_reconciled}/{total_txns})"
            )

    output_paths.append(("Excel", str(processed_files_excel_path)))
    output_paths.append(("Log", str(log_file_path)))
    elapsed = time.time() - start_time
    console.pipeline_footer(
        elapsed_seconds=elapsed,
        warnings=pipeline_warnings if pipeline_warnings else None,
        summary=summary if summary else None,
        output_paths=output_paths,
    )
    logger.debug("All steps completed successfully.")


__all__ = [
    "archive",
    "check",
    "copy_matching",
    "export_dates",
    "export_excel",
    "extract",
    "gmail",
    "merge_reconciled_attachments",
    "pipeline",
    "reconcile",
    "review",
    "rename",
    "sync",
    "validate_merged_pdf",
]
