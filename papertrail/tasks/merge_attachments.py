"""Merge attachment documents into target documents based on reconciliation matches."""

from pathlib import Path

from papertrail.logging_utils import get_logger
from papertrail.profiles import ExportMergeRule

logger = get_logger("merge-attachments")


def _match_type_pattern(doc_type: str, pattern: str) -> bool:
    """Check if doc_type matches a pattern with pipe-separated alternatives and trailing * wildcard."""
    doc_lower = doc_type.lower()
    for alt in pattern.split("|"):
        alt = alt.strip().lower()
        if alt.endswith("*"):
            if doc_lower.startswith(alt[:-1]):
                return True
        elif doc_lower == alt:
            return True
    return False


def merge_reconciled_attachments(
    export_path: Path,
    all_matches: list,
    merge_rules: list[ExportMergeRule],
) -> dict:
    """Merge attachment PDFs into target PDFs based on reconciliation match data.

    For each reconciled match, checks if any merge rule has both a target and attach
    type present among the matched candidates. When found, appends the attachment
    PDF pages to the target PDF in-place using pikepdf.

    Returns dict with merge stats: {"merged": int, "skipped": int, "errors": int}.
    """
    stats = {"merged": 0, "skipped": 0, "errors": 0}

    if not merge_rules or not all_matches:
        return stats

    # Track which attachment files have already been merged to avoid double-merging
    merged_attachments: set[str] = set()

    for match in all_matches:
        candidates = match.pdf_candidates

        for rule in merge_rules:
            # Find target and attachment candidates for this rule
            targets = [
                c for c in candidates
                if c.document_type and _match_type_pattern(c.document_type, rule.target_type)
                and not c.is_sub_document
            ]
            attachments = [
                c for c in candidates
                if c.document_type and _match_type_pattern(c.document_type, rule.attach_type)
                and not c.is_sub_document
            ]

            if not targets or not attachments:
                continue

            for target in targets:
                target_pdf = export_path / target.pdf_filename
                if not target_pdf.exists() or target_pdf.suffix.lower() != ".pdf":
                    continue

                for attachment in attachments:
                    # Skip if this attachment was already merged into another target
                    if attachment.pdf_filename in merged_attachments:
                        logger.debug(
                            f"[MERGE] Skipping {attachment.pdf_filename} — already merged elsewhere"
                        )
                        stats["skipped"] += 1
                        continue

                    attach_pdf = export_path / attachment.pdf_filename
                    if not attach_pdf.exists() or attach_pdf.suffix.lower() != ".pdf":
                        continue

                    # Skip if target and attachment are the same file
                    if target_pdf == attach_pdf:
                        continue

                    try:
                        import pikepdf

                        with pikepdf.open(target_pdf, allow_overwriting_input=True) as target_doc:
                            with pikepdf.open(attach_pdf) as attach_doc:
                                target_doc.pages.extend(attach_doc.pages)
                            target_doc.save(target_pdf)

                        merged_attachments.add(attachment.pdf_filename)
                        stats["merged"] += 1
                        logger.debug(
                            f"[MERGE] Appended {attachment.pdf_filename} "
                            f"to {target.pdf_filename}"
                        )
                    except Exception as e:
                        stats["errors"] += 1
                        logger.error(
                            f"[MERGE] Failed to append {attachment.pdf_filename} "
                            f"to {target.pdf_filename}: {e}"
                        )

    return stats
