"""Bank transaction reconciliation commands."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import openpyxl

from papertrail.llm import _extract_json_from_response
from papertrail.logging_utils import get_logger
from papertrail.repository import DocumentRepository
from papertrail.rules import RuleEngine
from papertrail.runtime import Runtime
from papertrail.utils import strip_diacritics

logger = get_logger("reconcile")

_AMOUNT_TOLERANCE = 0.01
_DATE_WINDOW_DAYS = 30


@dataclass
class Transaction:
    row_number: int
    date_posting: Optional[str]
    date_value: Optional[str]
    description: str
    amount: float
    currency: str
    notes: str
    treated: str


@dataclass
class PDFCandidate:
    json_path: Path
    pdf_filename: str
    date_issued: Optional[str]
    document_type: Optional[str]
    document_type_raw: Optional[str]
    document_title: Optional[str]
    issuing_party: Optional[str]
    total_amount: Optional[float]
    total_amount_currency: Optional[str]
    page_count: Optional[int] = None
    file_extension: Optional[str] = None
    hash_file: Optional[str] = None
    sub_doc_index: Optional[int] = None
    is_sub_document: bool = False
    exclude_from_matching: bool = False

    @property
    def candidate_id(self) -> str:
        base_id = self.hash_file or str(self.json_path)
        if self.sub_doc_index is not None:
            return f"{base_id}#sub{self.sub_doc_index}"
        return base_id

    @property
    def effective_document_type(self) -> Optional[str]:
        if (self.file_extension or "").lower() == ".pdf":
            raw_type = _normalize_for_match(self.document_type_raw or "")
            if raw_type in {"movimento", "notadelancamento"}:
                return "bank-note"
        return self.document_type


@dataclass
class MatchResult:
    transaction: Transaction
    pdf_candidates: list[PDFCandidate] = field(default_factory=list)
    method: str = ""
    confidence: float = 0.0
    reasoning: str = ""


def _coerce_amount(value) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_candidate(
    json_path: Path,
    pdf_filename: str,
    data: dict,
    *,
    document_title: Optional[str],
    page_count: Optional[int] = None,
    file_extension: Optional[str] = None,
    sub_doc_index: Optional[int] = None,
    is_sub_document: bool = False,
    exclude_from_matching: bool = False,
) -> PDFCandidate:
    return PDFCandidate(
        json_path=json_path,
        pdf_filename=pdf_filename,
        date_issued=data.get("date_issued"),
        document_type=data.get("document_type"),
        document_type_raw=data.get("document_type_raw"),
        document_title=document_title,
        issuing_party=data.get("issuing_party"),
        total_amount=_coerce_amount(data.get("total_amount")),
        total_amount_currency=data.get("total_amount_currency"),
        page_count=page_count,
        file_extension=file_extension,
        hash_file=data.get("hash_file"),
        sub_doc_index=sub_doc_index,
        is_sub_document=is_sub_document,
        exclude_from_matching=exclude_from_matching,
    )


def _transaction_category(txn: Transaction, rules: list) -> str:
    return _classify_transaction(txn, rules)[0]


def _serialize_match(match: MatchResult, *, errors: dict[int, list[str]], rules: list) -> dict:
    txn = match.transaction
    return {
        "row": txn.row_number,
        "date": txn.date_posting or txn.date_value,
        "description": txn.description,
        "amount": txn.amount,
        "currency": txn.currency,
        "transaction_category": _transaction_category(txn, rules),
        "method": match.method,
        "confidence": match.confidence,
        "reasoning": match.reasoning,
        "files": [candidate.pdf_filename for candidate in match.pdf_candidates],
        "errors": errors.get(txn.row_number, []),
    }


def _serialize_unmatched_transaction(txn: Transaction, rules: list) -> dict:
    return {
        "row": txn.row_number,
        "date": txn.date_posting or txn.date_value,
        "description": txn.description,
        "amount": txn.amount,
        "currency": txn.currency,
        "transaction_category": _transaction_category(txn, rules),
    }


def _serialize_unmatched_candidate(cand: PDFCandidate) -> dict:
    return {
        "file": cand.pdf_filename,
        "date_issued": cand.date_issued,
        "document_type": cand.effective_document_type or cand.document_type,
        "issuing_party": cand.issuing_party,
        "total_amount": cand.total_amount,
        "currency": cand.total_amount_currency,
    }


def _parse_date(value) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return text


def _load_transactions(excel_path: Path) -> list[Transaction]:
    import warnings

    warnings.filterwarnings("ignore", message="Workbook contains no default style")
    from papertrail.bank_statement.extractor import _PARSERS

    workbook = openpyxl.load_workbook(excel_path, data_only=True)
    worksheet = workbook.active

    selected_parser = None
    for parser in _PARSERS:
        if parser.can_parse(worksheet):
            selected_parser = parser
            break
    workbook.close()

    if selected_parser is None:
        logger.warning(f"No parser recognized format of {excel_path.name}")
        return []

    txn_dicts = selected_parser.load_transactions(excel_path)
    if txn_dicts is None:
        return []
    return [Transaction(**data) for data in txn_dicts]


def discover_bank_statements(repository: DocumentRepository, export_path: Path) -> list[Path]:
    statements = []
    for json_path, data in repository.iter_sidecars(export_path):
        if data.get("document_type") != "bank-statement":
            continue
        doc_path = repository.find_companion(json_path, data)
        if doc_path and doc_path.suffix.lower() == ".xlsx":
            statements.append(doc_path)
    return statements


def _latest_reconciliation_input_mtime(
    repository: DocumentRepository,
    export_path: Path,
) -> float:
    latest_mtime = 0.0
    for json_path, data in repository.iter_sidecars(export_path):
        latest_mtime = max(latest_mtime, json_path.stat().st_mtime)
        doc_path = repository.find_companion(json_path, data)
        if doc_path and doc_path.exists():
            latest_mtime = max(latest_mtime, doc_path.stat().st_mtime)
    return latest_mtime


def discover_statements_requiring_reconciliation(
    repository: DocumentRepository,
    export_path: Path,
    *,
    include_stale: bool = True,
) -> list[Path]:
    latest_input_mtime = _latest_reconciliation_input_mtime(repository, export_path)
    pending = []
    for statement_path in discover_bank_statements(repository, export_path):
        reconciliation_path = statement_path.with_suffix(".reconciliation.json")
        if not reconciliation_path.exists():
            pending.append(statement_path)
            continue
        if include_stale and reconciliation_path.stat().st_mtime < latest_input_mtime:
            pending.append(statement_path)
    return pending


def _load_pdf_candidates(
    repository: DocumentRepository,
    export_path: Path,
    *,
    exclude_prefixes: list[str] | None = None,
) -> list[PDFCandidate]:
    exclude_prefixes = exclude_prefixes or []
    candidates: list[PDFCandidate] = []
    seen_candidate_ids: set[str] = set()
    for json_path, data in repository.iter_sidecars(export_path):
        doc_path = repository.find_companion(json_path, data)
        if doc_path is None:
            continue
        if data.get("document_type") == "bank-statement" and doc_path.suffix.lower() == ".xlsx":
            continue

        excluded = any(doc_path.name.startswith(prefix) for prefix in exclude_prefixes)
        if excluded:
            logger.debug(f"[EXCLUDE-PREFIX] Skipping {doc_path.name} from matching")

        sub_docs = data.get("sub_documents")
        if sub_docs and len(sub_docs) >= 2:
            for index, sub_doc in enumerate(sub_docs):
                candidates.append(
                    _build_candidate(
                        json_path,
                        doc_path.name,
                        sub_doc,
                        document_title=None,
                        file_extension=doc_path.suffix.lower(),
                        sub_doc_index=index,
                        is_sub_document=True,
                        exclude_from_matching=excluded,
                    )
                )
                candidate = candidates[-1]
                if candidate.candidate_id in seen_candidate_ids:
                    candidates.pop()
                else:
                    seen_candidate_ids.add(candidate.candidate_id)

        candidate = _build_candidate(
            json_path,
            doc_path.name,
            data,
            document_title=data.get("document_title"),
            page_count=data.get("page_count"),
            file_extension=doc_path.suffix.lower(),
            exclude_from_matching=excluded,
        )
        if candidate.candidate_id in seen_candidate_ids:
            continue
        seen_candidate_ids.add(candidate.candidate_id)
        candidates.append(candidate)
    return candidates


def _days_between(date_str1: Optional[str], date_str2: Optional[str]) -> Optional[int]:
    if not date_str1 or not date_str2:
        return None
    try:
        left = datetime.strptime(date_str1, "%Y-%m-%d")
        right = datetime.strptime(date_str2, "%Y-%m-%d")
        return abs((left - right).days)
    except ValueError:
        return None


def _signed_days_between(date_str1: Optional[str], date_str2: Optional[str]) -> Optional[int]:
    if not date_str1 or not date_str2:
        return None
    try:
        left = datetime.strptime(date_str1, "%Y-%m-%d")
        right = datetime.strptime(date_str2, "%Y-%m-%d")
        return (right - left).days
    except ValueError:
        return None


def _candidate_signature(cand: PDFCandidate) -> tuple[str, str]:
    doc_type = (cand.effective_document_type or cand.document_type or "$unknown$").lower()
    issuing_party = _normalize_for_match(cand.issuing_party or "$unknown$")
    return doc_type, issuing_party


def _candidate_date_rank(txn_date: Optional[str], cand: PDFCandidate) -> Optional[tuple[int, bool, int]]:
    days = _days_between(txn_date, cand.date_issued)
    if days is None or days > _DATE_WINDOW_DAYS:
        return None
    signed_days = _signed_days_between(txn_date, cand.date_issued) or 0
    return (days, signed_days > 0, abs(signed_days))


def _phase1_deterministic_match(
    transactions: list[Transaction],
    candidates: list[PDFCandidate],
) -> tuple[list[MatchResult], list[Transaction], list[PDFCandidate]]:
    matches: list[MatchResult] = []
    unmatched: list[Transaction] = []
    used_candidates: set[str] = set()

    for txn in transactions:
        abs_amount = abs(txn.amount)
        amount_matches: dict[tuple[str, str], tuple[PDFCandidate, int, int]] = {}
        txn_date = txn.date_posting or txn.date_value
        for cand in candidates:
            if cand.total_amount is None:
                continue
            if abs(abs_amount - cand.total_amount) <= _AMOUNT_TOLERANCE:
                rank = _candidate_date_rank(txn_date, cand)
                if rank is not None:
                    key = _candidate_signature(cand)
                    current = amount_matches.get(key)
                    candidate_tuple = (cand, rank[0], _signed_days_between(txn_date, cand.date_issued) or 0)
                    if current is None:
                        amount_matches[key] = candidate_tuple
                        continue
                    _, current_days, current_signed = current
                    current_rank = (current_days, current_signed > 0, abs(current_signed))
                    if rank < current_rank:
                        amount_matches[key] = candidate_tuple

        if not amount_matches:
            unmatched.append(txn)
            continue

        selected_matches = sorted(
            amount_matches.values(),
            key=lambda item: (item[1], item[2] > 0, abs(item[2]), item[0].pdf_filename),
        )
        matched_pdfs = [candidate for candidate, _, _ in selected_matches]
        closest_days = selected_matches[0][1]

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

        matches.append(
            MatchResult(
                transaction=txn,
                pdf_candidates=matched_pdfs,
                method="exact",
                confidence=1.0,
                reasoning=reasoning,
            )
        )

    remaining = [candidate for candidate in candidates if candidate.candidate_id not in used_candidates]
    return matches, unmatched, remaining


def _format_candidate_for_llm(idx: int, cand: PDFCandidate) -> str:
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
    runtime: Runtime,
    transactions: list[Transaction],
    candidates: list[PDFCandidate],
) -> list[MatchResult]:
    if not transactions or not candidates or runtime.openai_client is None:
        return []

    txn_lines = []
    for index, txn in enumerate(transactions, 1):
        txn_date = txn.date_posting or txn.date_value or "unknown"
        txn_lines.append(f'[{index}] {txn_date} | {txn.amount:.2f} {txn.currency} | "{txn.description}"')

    cand_lines = []
    for index, candidate in enumerate(candidates):
        cand_lines.append(_format_candidate_for_llm(index, candidate))

    cand_labels = {}
    for index in range(len(candidates)):
        label = chr(ord("A") + index) if index < 26 else f"P{index}"
        cand_labels[label] = candidates[index]

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
        response = runtime.openai_client.chat.completions.create(
            model=runtime.model_id,
            max_tokens=4096,
            temperature=0,
            messages=[{"role": "user", "content": prompt}],
        )
        content = response.choices[0].message.content
        if not content:
            logger.warning("[PHASE-2] Empty LLM response")
            return []
        result = json.loads(_extract_json_from_response(content))
    except Exception as exc:
        logger.error(f"[PHASE-2] LLM matching failed: {exc}")
        return []

    matches: list[MatchResult] = []
    for match in result.get("matches", []):
        txn_idx = match.get("transaction_id")
        if txn_idx is None or txn_idx < 1 or txn_idx > len(transactions):
            continue

        txn = transactions[txn_idx - 1]
        matched_pdfs = []
        for pdf_id in match.get("pdf_ids", []):
            candidate = cand_labels.get(str(pdf_id).upper())
            if candidate:
                matched_pdfs.append(candidate)
        if not matched_pdfs:
            continue

        abs_amount = abs(txn.amount)
        has_amount_match = any(
            candidate.total_amount is not None
            and abs(abs_amount - candidate.total_amount) <= _AMOUNT_TOLERANCE
            for candidate in matched_pdfs
        )
        if not has_amount_match:
            logger.debug(
                f"[PHASE-2] Row {txn.row_number}: rejected LLM match — "
                f"no PDF has matching amount ({abs_amount:.2f})"
            )
            continue

        confidence = match.get("confidence", 0.5)
        reasoning = match.get("reasoning", "")
        logger.debug(
            f"[PHASE-2] Row {txn.row_number}: {txn.description[:50]} -> "
            f"{', '.join(candidate.pdf_filename for candidate in matched_pdfs)} "
            f"(confidence={confidence:.1f}, {reasoning})"
        )
        matches.append(
            MatchResult(
                transaction=txn,
                pdf_candidates=matched_pdfs,
                method="llm",
                confidence=confidence,
                reasoning=reasoning,
            )
        )

    return matches


def _normalize_for_match(text: str) -> str:
    return "".join(char for char in strip_diacritics(text).lower() if char.isalnum())


def _classify_transaction(txn: Transaction, rules: list) -> tuple[str, object | None]:
    return RuleEngine().classify_transaction(txn, rules)


def _validate_required_documents(matches: list[MatchResult], rules: list) -> dict[int, list[str]]:
    engine = RuleEngine()
    errors: dict[int, list[str]] = {}
    for match in matches:
        row_errors = engine.validate_match(match, rules)
        if row_errors:
            errors[match.transaction.row_number] = row_errors
    return errors


def _prune_unexpected_candidates(matches: list[MatchResult], rules: list) -> None:
    engine = RuleEngine()
    for match in matches:
        _, rule = _classify_transaction(match.transaction, rules)
        if rule is None:
            continue

        allowed_patterns = list(rule.required_types.keys()) + list(rule.shared_types.keys())
        if not allowed_patterns:
            continue

        filtered_candidates = []
        seen_candidate_ids: set[str] = set()
        for candidate in match.pdf_candidates:
            doc_type = engine.candidate_doc_type(candidate)
            if not doc_type:
                continue
            if not any(engine.match_doc_type(doc_type, pattern) for pattern in allowed_patterns):
                continue
            if candidate.candidate_id in seen_candidate_ids:
                continue
            seen_candidate_ids.add(candidate.candidate_id)
            filtered_candidates.append(candidate)

        if filtered_candidates:
            match.pdf_candidates = filtered_candidates


def _link_shared_documents(
    all_matches: list[MatchResult],
    final_unmatched: list[Transaction],
    all_candidates: list[PDFCandidate],
    rules: list,
) -> tuple[list[MatchResult], list[Transaction], set[str]]:
    shared_candidate_ids: set[str] = set()
    matched_by_row = {match.transaction.row_number: match for match in all_matches}
    all_txns = [match.transaction for match in all_matches] + final_unmatched

    newly_matched: list[MatchResult] = []
    still_unmatched_rows: set[int] = {txn.row_number for txn in final_unmatched}

    for txn in all_txns:
        category, rule = _classify_transaction(txn, rules)
        if rule is None or not rule.shared_types:
            continue

        txn_date = txn.date_posting or txn.date_value
        for type_pattern, issuing_party_filter in rule.shared_types.items():
            best_shared_match: tuple[PDFCandidate, tuple[int, bool, int]] | None = None
            for cand in all_candidates:
                doc_type = cand.effective_document_type
                if not doc_type or not RuleEngine().match_doc_type(doc_type, type_pattern):
                    continue
                if issuing_party_filter is not None and cand.issuing_party:
                    if _normalize_for_match(cand.issuing_party) != _normalize_for_match(issuing_party_filter):
                        continue
                rank = _candidate_date_rank(txn_date, cand)
                if rank is None:
                    continue

                if best_shared_match is None or rank < best_shared_match[1]:
                    best_shared_match = (cand, rank)

            shared_cands = [best_shared_match[0]] if best_shared_match else []

            if not shared_cands:
                continue

            for cand in shared_cands:
                shared_candidate_ids.add(cand.candidate_id)

            if txn.row_number in matched_by_row:
                match = matched_by_row[txn.row_number]
                existing_ids = {cand.candidate_id for cand in match.pdf_candidates}
                for cand in shared_cands:
                    if cand.candidate_id not in existing_ids:
                        match.pdf_candidates.append(cand)
                        existing_ids.add(cand.candidate_id)
            elif txn.row_number in still_unmatched_rows:
                newly_matched.append(
                    MatchResult(
                        transaction=txn,
                        pdf_candidates=list(shared_cands),
                        method="shared",
                        confidence=1.0,
                        reasoning=f"Shared {type_pattern} document(s)",
                    )
                )
                still_unmatched_rows.discard(txn.row_number)
                matched_by_row[txn.row_number] = newly_matched[-1]

    updated_matches = all_matches + newly_matched
    updated_unmatched = [txn for txn in final_unmatched if txn.row_number in still_unmatched_rows]
    return updated_matches, updated_unmatched, shared_candidate_ids


def _link_companion_documents(
    all_matches: list[MatchResult],
    final_unmatched: list[Transaction],
    all_candidates: list[PDFCandidate],
    rules: list,
) -> tuple[list[MatchResult], list[Transaction], set[str]]:
    companion_candidate_ids: set[str] = set()
    rules_with_companions = [rule for rule in rules if rule.companions]
    if not rules_with_companions:
        return all_matches, final_unmatched, companion_candidate_ids

    matched_by_row = {match.transaction.row_number: match for match in all_matches}
    all_txns = [match.transaction for match in all_matches] + final_unmatched
    txns_by_rule: dict[str, list[Transaction]] = {}
    for txn in all_txns:
        category, _ = _classify_transaction(txn, rules)
        txns_by_rule.setdefault(category, []).append(txn)

    already_matched_ids = {
        candidate.candidate_id
        for match in all_matches
        for candidate in match.pdf_candidates
    }

    newly_matched: list[MatchResult] = []
    still_unmatched_rows: set[int] = {txn.row_number for txn in final_unmatched}
    seen_groups: set[frozenset[int]] = set()

    for rule in rules_with_companions:
        companion_names = [rule.name] + rule.companions
        companion_txns: list[Transaction] = []
        for name in companion_names:
            companion_txns.extend(txns_by_rule.get(name, []))
        if len(companion_txns) < 2:
            continue

        by_date: dict[str, list[Transaction]] = {}
        for txn in companion_txns:
            date = txn.date_posting or txn.date_value
            if date:
                by_date.setdefault(date, []).append(txn)

        for date, group_txns in by_date.items():
            group_rules = {(_classify_transaction(txn, rules)[0]) for txn in group_txns}
            if len(group_rules) < 2:
                continue
            group_key = frozenset(txn.row_number for txn in group_txns)
            if group_key in seen_groups:
                continue
            seen_groups.add(group_key)

            group_sum = sum(abs(txn.amount) for txn in group_txns)
            matched_cand = None
            for cand in all_candidates:
                if cand.candidate_id in already_matched_ids:
                    continue
                if cand.total_amount is None:
                    continue
                if abs(group_sum - cand.total_amount) > _AMOUNT_TOLERANCE:
                    continue
                days = _days_between(date, cand.date_issued)
                if days is not None and days <= _DATE_WINDOW_DAYS:
                    matched_cand = cand
                    break

            if matched_cand is None:
                continue

            companion_candidate_ids.add(matched_cand.candidate_id)
            already_matched_ids.add(matched_cand.candidate_id)

            for txn in group_txns:
                if txn.row_number in matched_by_row:
                    match = matched_by_row[txn.row_number]
                    existing_ids = {cand.candidate_id for cand in match.pdf_candidates}
                    if matched_cand.candidate_id not in existing_ids:
                        match.pdf_candidates.append(matched_cand)
                elif txn.row_number in still_unmatched_rows:
                    new_match = MatchResult(
                        transaction=txn,
                        pdf_candidates=[matched_cand],
                        method="companion",
                        confidence=1.0,
                        reasoning=f"Companion sum match: {group_sum:.2f}",
                    )
                    newly_matched.append(new_match)
                    still_unmatched_rows.discard(txn.row_number)
                    matched_by_row[txn.row_number] = new_match

    updated_matches = all_matches + newly_matched
    updated_unmatched = [txn for txn in final_unmatched if txn.row_number in still_unmatched_rows]
    return updated_matches, updated_unmatched, companion_candidate_ids


def _write_reconciliation_file(
    excel_path: Path,
    matches: list[MatchResult],
    unmatched_transactions: list[Transaction],
    total_transactions: int,
    *,
    errors: dict[int, list[str]] | None = None,
    unmatched_files: list[PDFCandidate] | None = None,
    rules: list | None = None,
) -> Path:
    errors = errors or {}
    unmatched_files = unmatched_files or []
    rules = rules or []
    output_path = excel_path.with_suffix(".reconciliation.json")

    total_reconciled = sum(1 for match in matches if match.transaction.row_number not in errors)
    total_incomplete = sum(1 for match in matches if match.transaction.row_number in errors)
    total_unmatched = total_transactions - len(matches)
    reconciliation_rate = (total_reconciled / total_transactions * 100) if total_transactions > 0 else 0

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
        "matches": [_serialize_match(match, errors=errors, rules=rules) for match in matches],
        "unmatched": [_serialize_unmatched_transaction(txn, rules) for txn in unmatched_transactions],
        "unmatched_files": [_serialize_unmatched_candidate(cand) for cand in unmatched_files],
    }
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return output_path


def reconcile_single(
    runtime: Runtime,
    repository: DocumentRepository,
    export_path: Path,
    excel_path: Path,
    *,
    dry_run: bool,
    quiet: bool = False,
) -> dict:
    console = runtime.console
    empty_stats = {
        "total": 0,
        "reconciled": 0,
        "unmatched": 0,
        "incomplete": 0,
        "unmatched_files": 0,
        "reconciliation_rate": 0.0,
    }

    transactions = _load_transactions(excel_path)
    if not transactions:
        if not quiet:
            console.warning("No untreated transactions found")
        return empty_stats

    profile = runtime.profile
    exclude_prefixes = profile.reconciliation.exclude_prefixes
    candidates = _load_pdf_candidates(repository, export_path, exclude_prefixes=exclude_prefixes)
    matchable = [candidate for candidate in candidates if not candidate.exclude_from_matching]

    p1_matches, unmatched_txns, remaining_cands = _phase1_deterministic_match(transactions, matchable)
    p2_matches = _phase2_llm_match(runtime, unmatched_txns, remaining_cands) if unmatched_txns and remaining_cands else []
    all_matches = p1_matches + p2_matches

    p2_matched_rows = {match.transaction.row_number for match in p2_matches}
    final_unmatched = [txn for txn in unmatched_txns if txn.row_number not in p2_matched_rows]

    rules = profile.reconciliation.rules
    all_matches, final_unmatched, companion_candidate_ids = _link_companion_documents(
        all_matches,
        final_unmatched,
        matchable,
        rules,
    )
    all_matches, final_unmatched, shared_candidate_ids = _link_shared_documents(
        all_matches,
        final_unmatched,
        matchable,
        rules,
    )
    _prune_unexpected_candidates(all_matches, rules)

    candidate_match_counts: dict[str, int] = {}
    for match in all_matches:
        for cand in match.pdf_candidates:
            candidate_match_counts[cand.candidate_id] = candidate_match_counts.get(cand.candidate_id, 0) + 1
    for candidate_id, count in candidate_match_counts.items():
        if count > 1:
            is_sub_doc = any(
                candidate.candidate_id == candidate_id and candidate.is_sub_document
                for match in all_matches
                for candidate in match.pdf_candidates
            )
            if not is_sub_doc and candidate_id not in shared_candidate_ids and candidate_id not in companion_candidate_ids:
                logger.warning(f"[REDUNDANT-MATCH] {candidate_id} matched {count} transactions")

    matched_candidate_ids = {
        candidate.candidate_id
        for match in all_matches
        for candidate in match.pdf_candidates
    }
    unmatched_files = [candidate for candidate in candidates if candidate.candidate_id not in matched_candidate_ids]

    validation_errors = _validate_required_documents(all_matches, rules)
    for row_num, row_errors in validation_errors.items():
        txn = next(match.transaction for match in all_matches if match.transaction.row_number == row_num)
        category, _ = _classify_transaction(txn, rules)
        logger.debug(f"[INCOMPLETE] Row {row_num} ({category}): {', '.join(row_errors)}")

    if not dry_run:
        _write_reconciliation_file(
            excel_path,
            all_matches,
            final_unmatched,
            len(transactions),
            errors=validation_errors,
            unmatched_files=unmatched_files,
            rules=rules,
        )
    elif not quiet:
        console.detail(f"Dry run: would write {len(all_matches)} matches to .reconciliation file")

    total_txns = len(transactions)
    total_unmatched = len(final_unmatched)
    total_incomplete = len(validation_errors)
    total_reconciled = len(all_matches) - total_incomplete
    total_unmatched_files = len(unmatched_files)
    pct = (total_reconciled / total_txns * 100) if total_txns > 0 else 0

    if not quiet:
        console.info("")
        console.success(f"{total_reconciled}/{total_txns} transactions reconciled ({pct:.1f}%)", indent=False)
        if total_incomplete > 0:
            console.warning(f"{total_incomplete} matched transactions with errors", indent=False)
            for row_num, row_errors in validation_errors.items():
                console.detail(f"Row {row_num}: {', '.join(row_errors)}")
        if total_unmatched > 0:
            console.warning(f"{total_unmatched} transactions unmatched", indent=False)
            for txn in final_unmatched:
                console.detail(f"Row {txn.row_number}: {txn.description[:50]} ({txn.amount:.2f} {txn.currency})")
        if total_unmatched_files > 0:
            console.warning(f"{total_unmatched_files} document files unmatched", indent=False)
            for cand in unmatched_files:
                amount_str = ""
                if cand.total_amount is not None:
                    currency = cand.total_amount_currency or "EUR"
                    amount_str = f" ({cand.total_amount:.2f} {currency})"
                console.detail(f"{cand.pdf_filename}{amount_str}")

    return {
        "total": total_txns,
        "reconciled": total_reconciled,
        "unmatched": total_unmatched,
        "incomplete": total_incomplete,
        "unmatched_files": total_unmatched_files,
        "reconciliation_rate": pct,
        "matches": all_matches,
    }
