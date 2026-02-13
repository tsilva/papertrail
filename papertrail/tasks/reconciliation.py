"""Bank transaction reconciliation: matches bank transactions to PDF documents."""

import json
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl

from papertrail.config import get_current_profile, get_openai_client
from papertrail.console import get_console
from papertrail.llm import _extract_json_from_response
from papertrail.logging_utils import get_logger
from papertrail.metadata import iter_json_files, find_companion_file
from papertrail.tasks import task_log_context

logger = get_logger("reconcile")

# Excel layout constants (Millennium BCP format)
_HEADER_ROW = 8  # Row with column headers
_DATA_START_ROW = 9  # First data row
_COL_DATA_LANCAMENTO = 1  # A
_COL_DATA_VALOR = 2  # B
_COL_DESCRICAO = 3  # C
_COL_MONTANTE = 4  # D
_COL_MOEDA = 5  # E
_COL_NOTAS = 6  # F
_COL_TRATADO = 7  # G

# Matching parameters
_AMOUNT_TOLERANCE = 0.01
_DATE_WINDOW_DAYS = 30


@dataclass
class Transaction:
    """A bank transaction row."""

    row_number: int
    date_posting: Optional[str]  # Data Lancamento
    date_value: Optional[str]  # Data Valor
    description: str
    amount: float  # Negative in bank export
    currency: str
    notes: str
    treated: str  # "Sim" or "Nao"


@dataclass
class PDFCandidate:
    """A PDF document available for matching."""

    json_path: Path
    pdf_filename: str
    date_issued: Optional[str]
    document_type: Optional[str]
    document_title: Optional[str]
    issuing_party: Optional[str]
    total_amount: Optional[float]
    total_amount_currency: Optional[str]
    sub_doc_index: Optional[int] = None
    is_sub_document: bool = False

    @property
    def candidate_id(self) -> str:
        if self.sub_doc_index is not None:
            return f"{self.json_path}#sub{self.sub_doc_index}"
        return str(self.json_path)


@dataclass
class MatchResult:
    """A match between a transaction and PDFs."""

    transaction: Transaction
    pdf_candidates: list[PDFCandidate] = field(default_factory=list)
    method: str = ""  # "exact" or "llm"
    confidence: float = 0.0
    reasoning: str = ""


def _parse_date(value) -> Optional[str]:
    """Parse a date cell value to YYYY-MM-DD string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if not s:
        return None
    # Try common formats
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _load_transactions(excel_path: Path) -> list[Transaction]:
    """Load untreated transactions from a Millennium BCP Excel export."""
    import warnings
    warnings.filterwarnings("ignore", message="Workbook contains no default style")
    wb = openpyxl.load_workbook(excel_path, data_only=True)
    ws = wb.active

    transactions = []
    for row in ws.iter_rows(min_row=_DATA_START_ROW, max_col=_COL_TRATADO):
        if row[_COL_DESCRICAO - 1].value is None:
            continue

        treated_val = str(row[_COL_TRATADO - 1].value or "").strip()

        if treated_val.lower() not in ("nao", "não", ""):
            continue

        amount_raw = row[_COL_MONTANTE - 1].value
        if amount_raw is None:
            continue

        try:
            amount = float(amount_raw)
        except (ValueError, TypeError):
            continue

        transactions.append(Transaction(
            row_number=row[0].row,
            date_posting=_parse_date(row[_COL_DATA_LANCAMENTO - 1].value),
            date_value=_parse_date(row[_COL_DATA_VALOR - 1].value),
            description=str(row[_COL_DESCRICAO - 1].value or "").strip(),
            amount=amount,
            currency=str(row[_COL_MOEDA - 1].value or "EUR").strip(),
            notes=str(row[_COL_NOTAS - 1].value or "").strip(),
            treated=treated_val,
        ))

    wb.close()
    return transactions


def _discover_bank_statements(export_path: Path) -> list[Path]:
    """Find XLSX files in export_path where sidecar JSON has document_type == 'bank-statement'."""
    statements = []
    for json_path, data in iter_json_files(export_path):
        if data.get("document_type") != "bank-statement":
            continue
        doc_path = find_companion_file(json_path, data)
        if doc_path and doc_path.suffix.lower() == ".xlsx":
            statements.append(doc_path)
    return statements


def _load_pdf_candidates(export_path: Path) -> list[PDFCandidate]:
    """Load document candidates from JSON sidecar metadata in export folder.

    Excludes bank-statement entries (they are the source, not candidates).
    When a document has sub_documents (2+ entries), expands each sub-document
    as an independent candidate (parent is skipped as a candidate).
    """
    candidates = []
    for json_path, data in iter_json_files(export_path):
        if data.get("document_type") == "bank-statement":
            continue

        doc_path = find_companion_file(json_path, data)
        if doc_path is None:
            continue

        # Expand sub-documents as individual candidates
        sub_docs = data.get("sub_documents")
        if sub_docs and len(sub_docs) >= 2:
            for i, sd in enumerate(sub_docs):
                sd_amount = sd.get("total_amount")
                if sd_amount is not None:
                    try:
                        sd_amount = float(sd_amount)
                    except (ValueError, TypeError):
                        sd_amount = None

                candidates.append(PDFCandidate(
                    json_path=json_path,
                    pdf_filename=doc_path.name,
                    date_issued=sd.get("date_issued"),
                    document_type=sd.get("document_type"),
                    document_title=None,
                    issuing_party=sd.get("issuing_party"),
                    total_amount=sd_amount,
                    total_amount_currency=sd.get("total_amount_currency"),
                    sub_doc_index=i,
                    is_sub_document=True,
                ))
            continue  # Skip parent as candidate

        total_amount = data.get("total_amount")
        if total_amount is not None:
            try:
                total_amount = float(total_amount)
            except (ValueError, TypeError):
                total_amount = None

        candidates.append(PDFCandidate(
            json_path=json_path,
            pdf_filename=doc_path.name,
            date_issued=data.get("date_issued"),
            document_type=data.get("document_type"),
            document_title=data.get("document_title"),
            issuing_party=data.get("issuing_party"),
            total_amount=total_amount,
            total_amount_currency=data.get("total_amount_currency"),
        ))

    return candidates


def _days_between(date_str1: Optional[str], date_str2: Optional[str]) -> Optional[int]:
    """Calculate days between two YYYY-MM-DD date strings."""
    if not date_str1 or not date_str2:
        return None
    try:
        d1 = datetime.strptime(date_str1, "%Y-%m-%d")
        d2 = datetime.strptime(date_str2, "%Y-%m-%d")
        return abs((d1 - d2).days)
    except ValueError:
        return None


def _phase1_deterministic_match(
    transactions: list[Transaction],
    candidates: list[PDFCandidate],
) -> tuple[list[MatchResult], list[Transaction], list[PDFCandidate]]:
    """Match transactions to PDFs by amount + date proximity."""
    matches: list[MatchResult] = []
    unmatched: list[Transaction] = []
    used_candidates: set[str] = set()  # candidate_id strings

    for txn in transactions:
        abs_amount = abs(txn.amount)

        amount_matches = []
        for cand in candidates:
            if cand.total_amount is None:
                continue
            if abs(abs_amount - cand.total_amount) <= _AMOUNT_TOLERANCE:
                txn_date = txn.date_posting or txn.date_value
                days = _days_between(txn_date, cand.date_issued)
                if days is not None and days <= _DATE_WINDOW_DAYS:
                    amount_matches.append((cand, days))

        if not amount_matches:
            unmatched.append(txn)
            continue

        amount_matches.sort(key=lambda x: x[1])
        matched_pdfs = [cand for cand, _ in amount_matches]
        closest_days = amount_matches[0][1]

        for cand in matched_pdfs:
            used_candidates.add(cand.candidate_id)

        reasoning = (
            f"Amount match: {abs_amount:.2f} "
            f"({len(matched_pdfs)} PDF(s), closest date: {closest_days}d)"
        )
        logger.debug(
            f"[PHASE-1] Row {txn.row_number}: {txn.description[:50]} -> "
            f"{', '.join(c.pdf_filename for c in matched_pdfs)} ({reasoning})"
        )

        matches.append(MatchResult(
            transaction=txn,
            pdf_candidates=matched_pdfs,
            method="exact",
            confidence=1.0,
            reasoning=reasoning,
        ))

    remaining = [c for c in candidates if c.candidate_id not in used_candidates]

    return matches, unmatched, remaining


def _format_candidate_for_llm(idx: int, cand: PDFCandidate) -> str:
    """Format a PDF candidate for the LLM prompt."""
    parts = [cand.pdf_filename]
    if cand.sub_doc_index is not None:
        parts.append(f"(sub-doc #{cand.sub_doc_index})")
    if cand.issuing_party and cand.issuing_party != "$UNKNOWN$":
        parts.append(cand.issuing_party)
    if cand.document_title:
        parts.append(cand.document_title)
    if cand.total_amount is not None:
        currency = cand.total_amount_currency or "EUR"
        parts.append(f"{cand.total_amount:.2f} {currency}")
    if cand.date_issued and cand.date_issued != "$UNKNOWN$":
        parts.append(cand.date_issued)

    label = chr(ord("A") + idx) if idx < 26 else f"P{idx}"
    return f"[{label}] {' - '.join(parts)}"


def _phase2_llm_match(
    transactions: list[Transaction],
    candidates: list[PDFCandidate],
    client,
    model_id: str,
) -> list[MatchResult]:
    """LLM-assisted fuzzy matching for remaining transactions."""
    if not transactions or not candidates:
        return []

    txn_lines = []
    for i, txn in enumerate(transactions, 1):
        txn_date = txn.date_posting or txn.date_value or "unknown"
        txn_lines.append(
            f"[{i}] {txn_date} | {txn.amount:.2f} {txn.currency} | \"{txn.description}\""
        )

    cand_lines = []
    for i, cand in enumerate(candidates):
        cand_lines.append(_format_candidate_for_llm(i, cand))

    cand_labels = {}
    for i in range(len(candidates)):
        label = chr(ord("A") + i) if i < 26 else f"P{i}"
        cand_labels[label] = candidates[i]

    prompt = f"""You are a bank reconciliation assistant. Match bank transactions to their supporting PDF documents.

UNMATCHED TRANSACTIONS:
{chr(10).join(txn_lines)}

AVAILABLE PDF DOCUMENTS:
{chr(10).join(cand_lines)}

Match transactions to PDFs. Consider:
- Bank descriptions are abbreviated; match to issuing_party and document_title
- Amounts may differ slightly (fees, taxes included)
- Dates may differ by up to 30 days
- Some transactions may have NO match — do not force matches
- One transaction CAN match multiple PDFs (e.g., bank note + vendor invoice)

Respond in JSON:
{{
    "matches": [
        {{
            "transaction_id": 1,
            "pdf_ids": ["A", "B"],
            "confidence": 0.9,
            "reasoning": "Brief explanation"
        }}
    ],
    "unmatched_transactions": [2, 3]
}}"""

    try:
        response = client.chat.completions.create(
            model=model_id,
            max_tokens=4096,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )

        content = response.choices[0].message.content
        if not content:
            logger.warning("[PHASE-2] Empty LLM response")
            return []

        content = _extract_json_from_response(content)
        result = json.loads(content)

    except Exception as e:
        logger.error(f"[PHASE-2] LLM matching failed: {e}")
        return []

    matches: list[MatchResult] = []
    for m in result.get("matches", []):
        txn_idx = m.get("transaction_id")
        if txn_idx is None or txn_idx < 1 or txn_idx > len(transactions):
            continue

        txn = transactions[txn_idx - 1]
        pdf_ids = m.get("pdf_ids", [])
        matched_pdfs = []
        for pid in pdf_ids:
            cand = cand_labels.get(str(pid).upper())
            if cand:
                matched_pdfs.append(cand)

        if not matched_pdfs:
            continue

        # Require at least one matched PDF to have a matching amount
        abs_amount = abs(txn.amount)
        has_amount_match = any(
            c.total_amount is not None
            and abs(abs_amount - c.total_amount) <= _AMOUNT_TOLERANCE
            for c in matched_pdfs
        )
        if not has_amount_match:
            logger.debug(
                f"[PHASE-2] Row {txn.row_number}: rejected LLM match — "
                f"no PDF has matching amount ({abs_amount:.2f})"
            )
            continue

        confidence = m.get("confidence", 0.5)
        reasoning = m.get("reasoning", "")

        logger.debug(
            f"[PHASE-2] Row {txn.row_number}: {txn.description[:50]} -> "
            f"{', '.join(c.pdf_filename for c in matched_pdfs)} "
            f"(confidence={confidence:.1f}, {reasoning})"
        )

        matches.append(MatchResult(
            transaction=txn,
            pdf_candidates=matched_pdfs,
            method="llm",
            confidence=confidence,
            reasoning=reasoning,
        ))

    return matches


def _strip_diacritics(s: str) -> str:
    """Remove diacritics/accents from a string."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _match_type_pattern(doc_type: str, pattern: str) -> bool:
    """Check if doc_type matches a pipe-separated pattern (case-insensitive)."""
    doc_lower = doc_type.lower()
    return any(alt.strip().lower() == doc_lower for alt in pattern.split("|"))


def _parse_cardinality(value) -> tuple[int, int | None]:
    """Parse cardinality spec into (min, max). None max means unbounded.

    Examples: 1 → (1,1), [0,1] → (0,1), [1,null] → (1,None)
    """
    if isinstance(value, int):
        return (value, value)
    if isinstance(value, list) and len(value) == 2:
        lo = value[0] if value[0] is not None else 0
        hi = value[1]  # None means unbounded
        return (int(lo), int(hi) if hi is not None else None)
    return (0, None)


def _classify_transaction(
    txn: Transaction,
    rules: list,
) -> tuple[str, object | None]:
    """Classify transaction by first-match-wins rules. Returns (category_name, rule)."""
    normalized = _strip_diacritics(txn.description).upper()
    for rule in rules:
        # Check direction filter
        if rule.direction is not None:
            if rule.direction == "credit" and txn.amount <= 0:
                continue
            if rule.direction == "debit" and txn.amount > 0:
                continue
        # Check description keywords
        if rule.match_description:
            if not any(kw.upper() in normalized for kw in rule.match_description):
                continue
        return (rule.name, rule)
    return ("unclassified", None)


def _validate_required_documents(
    matches: list[MatchResult],
    rules: list,
) -> dict[int, list[str]]:
    """Validate matched documents against rule requirements. Returns {row: [errors]}."""
    errors: dict[int, list[str]] = {}
    for m in matches:
        category, rule = _classify_transaction(m.transaction, rules)
        if rule is None:
            errors[m.transaction.row_number] = [f"unclassified transaction"]
            continue

        row_errors: list[str] = []
        for pattern, cardinality in rule.required_types.items():
            min_count, max_count = _parse_cardinality(cardinality)
            count = sum(
                1 for c in m.pdf_candidates
                if c.document_type and _match_type_pattern(c.document_type, pattern)
            )
            display_pattern = pattern.replace("|", "/")
            if count < min_count:
                row_errors.append(f"missing {display_pattern} (expected {min_count}, found {count})")
            elif max_count is not None and count > max_count:
                row_errors.append(f"too many {display_pattern} (expected max {max_count}, found {count})")

        if row_errors:
            errors[m.transaction.row_number] = row_errors
    return errors


def _write_reconciliation_file(
    excel_path: Path,
    matches: list[MatchResult],
    unmatched_transactions: list[Transaction],
    total_transactions: int,
    errors: dict[int, list[str]] | None = None,
    unmatched_files: list[PDFCandidate] | None = None,
    rules: list | None = None,
) -> Path:
    """Write a .reconciliation JSON sidecar alongside the XLSX. Returns the output path."""
    if errors is None:
        errors = {}
    if unmatched_files is None:
        unmatched_files = []
    if rules is None:
        rules = []

    output_path = excel_path.with_suffix(".reconciliation.json")

    total_reconciled = sum(1 for m in matches if m.transaction.row_number not in errors)
    total_incomplete = sum(1 for m in matches if m.transaction.row_number in errors)
    total_unmatched = total_transactions - len(matches)
    reconciliation_rate = (total_reconciled / total_transactions * 100) if total_transactions > 0 else 0

    match_entries = []
    for m in matches:
        txn = m.transaction
        row_errors = errors.get(txn.row_number, [])
        category, _ = _classify_transaction(txn, rules)
        match_entries.append({
            "row": txn.row_number,
            "date": txn.date_posting or txn.date_value,
            "description": txn.description,
            "amount": txn.amount,
            "currency": txn.currency,
            "transaction_category": category,
            "method": m.method,
            "confidence": m.confidence,
            "reasoning": m.reasoning,
            "files": [c.pdf_filename for c in m.pdf_candidates],
            "errors": row_errors,
        })

    unmatched_entries = []
    for txn in unmatched_transactions:
        category, _ = _classify_transaction(txn, rules)
        unmatched_entries.append({
            "row": txn.row_number,
            "date": txn.date_posting or txn.date_value,
            "description": txn.description,
            "amount": txn.amount,
            "currency": txn.currency,
            "transaction_category": category,
        })

    unmatched_file_entries = []
    for cand in unmatched_files:
        unmatched_file_entries.append({
            "file": cand.pdf_filename,
            "date_issued": cand.date_issued,
            "document_type": cand.document_type,
            "issuing_party": cand.issuing_party,
            "total_amount": cand.total_amount,
            "currency": cand.total_amount_currency,
        })

    data = {
        "source": excel_path.name,
        "generated": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "total": total_transactions,
            "reconciled": total_reconciled,
            "incomplete": total_incomplete,
            "unmatched": total_unmatched,
            "unmatched_files": len(unmatched_files),
            "reconciliation_rate": round(reconciliation_rate, 1),
        },
        "matches": match_entries,
        "unmatched": unmatched_entries,
        "unmatched_files": unmatched_file_entries,
    }

    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return output_path


def _reconcile_single(
    export_path: Path,
    excel_path: Path,
    dry_run: bool,
    console,
    quiet: bool = False,
) -> dict:
    """Reconcile a single bank statement against PDF documents in export_path."""
    empty_stats = {"total": 0, "reconciled": 0, "unmatched": 0, "incomplete": 0, "unmatched_files": 0, "reconciliation_rate": 0.0}

    transactions = _load_transactions(excel_path)

    if not transactions:
        if not quiet:
            console.warning("No untreated transactions found")
        return empty_stats

    candidates = _load_pdf_candidates(export_path)

    if not candidates:
        if not quiet:
            console.warning("No document candidates found")
        return empty_stats

    p1_matches, unmatched_txns, remaining_cands = (
        _phase1_deterministic_match(transactions, candidates)
    )

    p2_matches: list[MatchResult] = []
    if unmatched_txns and remaining_cands:
        profile = get_current_profile()
        model_id = profile.openrouter.model_id
        client = get_openai_client()
        p2_matches = _phase2_llm_match(
            unmatched_txns, remaining_cands, client, model_id,
        )

    all_matches = p1_matches + p2_matches

    # Build final unmatched list (phase-1 unmatched minus phase-2 matched)
    p2_matched_rows = {m.transaction.row_number for m in p2_matches}
    final_unmatched = [txn for txn in unmatched_txns if txn.row_number not in p2_matched_rows]

    # Redundancy detection: warn if a non-sub-doc candidate matches multiple transactions
    candidate_match_counts: dict[str, int] = {}
    for m in all_matches:
        for cand in m.pdf_candidates:
            candidate_match_counts[cand.candidate_id] = candidate_match_counts.get(cand.candidate_id, 0) + 1
    for cid, count in candidate_match_counts.items():
        if count > 1:
            is_sub_doc = any(
                c.candidate_id == cid and c.is_sub_document
                for m in all_matches for c in m.pdf_candidates
            )
            if not is_sub_doc:
                logger.warning(f"[REDUNDANT-MATCH] {cid} matched {count} transactions")

    # Build set of all matched PDF candidate_ids
    matched_candidate_ids = set()
    for m in all_matches:
        for c in m.pdf_candidates:
            matched_candidate_ids.add(c.candidate_id)
    unmatched_files = [c for c in candidates if c.candidate_id not in matched_candidate_ids]

    # Validate required document types per transaction category
    profile = get_current_profile()
    rules = profile.reconciliation.rules
    validation_errors = _validate_required_documents(all_matches, rules)
    for row_num, row_errors in validation_errors.items():
        txn = next(m.transaction for m in all_matches if m.transaction.row_number == row_num)
        category, _ = _classify_transaction(txn, rules)
        logger.debug(
            f"[INCOMPLETE] Row {row_num} ({category}): {', '.join(row_errors)}"
        )

    if all_matches and not dry_run:
        _write_reconciliation_file(
            excel_path, all_matches, final_unmatched, len(transactions),
            errors=validation_errors,
            unmatched_files=unmatched_files,
            rules=rules,
        )
    elif dry_run and all_matches:
        if not quiet:
            console.detail(f"Dry run: would write {len(all_matches)} matches to .reconciliation file")

    total_txns = len(transactions)
    total_matched = len(all_matches)
    total_unmatched = len(final_unmatched)
    total_incomplete = len(validation_errors)
    total_reconciled = total_matched - total_incomplete
    total_unmatched_files = len(unmatched_files)
    pct = (total_reconciled / total_txns * 100) if total_txns > 0 else 0

    logger.debug(
        f"[SUMMARY] {total_reconciled}/{total_txns} reconciled ({pct:.1f}%), "
        f"{total_incomplete} incomplete, {total_unmatched} unmatched, "
        f"{total_unmatched_files} unmatched files"
    )

    if not quiet:
        console.info("")
        console.success(
            f"{total_reconciled}/{total_txns} transactions reconciled ({pct:.1f}%)",
            indent=False,
        )
        if total_incomplete > 0:
            console.warning(
                f"{total_incomplete} matched transactions with errors",
                indent=False,
            )
            for row_num, row_errors in validation_errors.items():
                console.detail(
                    f"Row {row_num}: {', '.join(row_errors)}"
                )
        if total_unmatched > 0:
            console.warning(
                f"{total_unmatched} transactions unmatched", indent=False,
            )
            for txn in final_unmatched:
                console.detail(
                    f"Row {txn.row_number}: {txn.description[:50]} "
                    f"({txn.amount:.2f} {txn.currency})"
                )

    for txn in final_unmatched:
        category, _ = _classify_transaction(txn, rules)
        logger.debug(
            f"[NO-MATCH] Row {txn.row_number} ({category}): {txn.description[:50]} "
            f"({txn.amount:.2f} {txn.currency})"
        )

    if not quiet and total_unmatched_files > 0:
        console.warning(
            f"{total_unmatched_files} document files unmatched", indent=False,
        )
        for cand in unmatched_files:
            amount_str = ""
            if cand.total_amount is not None:
                currency = cand.total_amount_currency or "EUR"
                amount_str = f" ({cand.total_amount:.2f} {currency})"
            console.detail(f"{cand.pdf_filename}{amount_str}")

    for cand in unmatched_files:
        logger.debug(
            f"[NO-MATCH-FILE] {cand.pdf_filename} "
            f"(type={cand.document_type}, party={cand.issuing_party})"
        )

    return {
        "total": total_txns,
        "reconciled": total_reconciled,
        "unmatched": total_unmatched,
        "incomplete": total_incomplete,
        "unmatched_files": total_unmatched_files,
        "reconciliation_rate": pct,
    }


def task_reconcile(
    export_path: Path,
    excel_path: Optional[Path] = None,
    dry_run: bool = False,
) -> None:
    """Reconcile bank transactions against PDF documents.

    If excel_path is given, reconcile that specific file.
    Otherwise, auto-discover bank statements in export_path by document_type.
    Falls back to export_path/transactions.xlsx for backward compat.
    """
    console = get_console()

    with task_log_context(export_path, "reconcile") as log_file:
        # Determine which XLSX files to reconcile
        if excel_path is not None:
            excel_paths = [excel_path]
        else:
            excel_paths = _discover_bank_statements(export_path)
            if not excel_paths:
                # Backward compat fallback
                fallback = export_path / "transactions.xlsx"
                if fallback.exists():
                    excel_paths = [fallback]

        if not excel_paths:
            console.warning("No bank statements found to reconcile", indent=False)
            return

        for ep in excel_paths:
            if not ep.exists():
                console.error(f"Excel file not found: {ep}", indent=False)
                continue

            with console.task(f"Reconcile: {ep.name}"):
                _reconcile_single(export_path, ep, dry_run, console)
