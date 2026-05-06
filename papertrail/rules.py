"""Shared rule matching for export and reconciliation flows."""

from __future__ import annotations

from typing import Optional

from papertrail.utils import strip_diacritics


_DOC_TYPE_PATTERN_ALIASES = {
    "bank-note": {"bank-card-transaction"},
}


class RuleEngine:
    """Unified rule evaluation across export and reconciliation."""

    def __init__(self, profile=None, profile_context: Optional[dict] = None):
        self.profile = profile
        self.profile_context = profile_context or {}

    def metadata_has_qrcode(self, metadata: dict) -> bool:
        """Return true when a document or any sub-document has QR metadata."""
        if metadata.get("qrcode"):
            return True
        sub_documents = metadata.get("sub_documents")
        if not isinstance(sub_documents, list):
            return False
        return any(
            isinstance(sub_doc, dict) and bool(sub_doc.get("qrcode"))
            for sub_doc in sub_documents
        )

    def get_nested_value(self, metadata: dict, key: str):
        """Get a nested value using dot notation."""
        if key == "has_qrcode":
            return self.metadata_has_qrcode(metadata)
        current = metadata
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    def resolve_profile_value(self, pattern: str):
        """Resolve ${profile.*} values using profile_context."""
        if not isinstance(pattern, str):
            return pattern
        if not pattern.startswith("${profile.") or not pattern.endswith("}"):
            return pattern
        key = pattern[len("${profile."):-1]
        return self.profile_context.get(key, pattern)

    def match_value(self, actual, pattern: object) -> bool:
        """Match a value against a wildcard or numeric pattern."""
        if actual is None:
            return False

        resolved_pattern = self.resolve_profile_value(pattern)
        if isinstance(actual, bool) or isinstance(resolved_pattern, bool):
            if isinstance(resolved_pattern, bool):
                expected_bool = resolved_pattern
            elif isinstance(resolved_pattern, str) and resolved_pattern.lower() in {"true", "false"}:
                expected_bool = resolved_pattern.lower() == "true"
            else:
                return False
            return bool(actual) is expected_bool

        pattern = str(resolved_pattern)
        for operator in (">=", "<=", "!=", ">", "<"):
            if pattern.startswith(operator):
                try:
                    actual_float = float(actual)
                    expected_float = float(pattern[len(operator):])
                except (TypeError, ValueError):
                    return False
                return {
                    ">": actual_float > expected_float,
                    "<": actual_float < expected_float,
                    ">=": actual_float >= expected_float,
                    "<=": actual_float <= expected_float,
                    "!=": actual_float != expected_float,
                }[operator]

        actual_str = str(actual).lower()
        pattern_str = pattern.lower()
        if pattern_str.endswith("*"):
            return actual_str.startswith(pattern_str[:-1])
        if isinstance(actual, (int, float)):
            try:
                return float(actual) == float(pattern)
            except (TypeError, ValueError):
                return False
        return actual_str == pattern_str

    def match_doc_type(self, doc_type: str, pattern: str) -> bool:
        """Check if a document type matches a pipe-separated pattern."""
        if not doc_type:
            return False
        doc_lower = doc_type.lower()
        for alternative in pattern.split("|"):
            alt = alternative.strip().lower()
            if alt.endswith("*"):
                if doc_lower.startswith(alt[:-1]):
                    return True
            elif doc_lower == alt or doc_lower in _DOC_TYPE_PATTERN_ALIASES.get(
                alt, set()
            ):
                return True
        return False

    def candidate_doc_type(self, candidate) -> Optional[str]:
        """Resolve the effective document type for reconciliation candidates."""
        return getattr(candidate, "effective_document_type", None) or getattr(candidate, "document_type", None)

    def evaluate_export_prefix(self, metadata: dict, file_mappings=None) -> str:
        """Evaluate file mapping rules. First match wins."""
        if file_mappings is None:
            if self.profile is None:
                raise RuntimeError("RuleEngine requires a profile or file_mappings")
            config = self.profile.export.file_mappings
        else:
            config = file_mappings
        for rule in config.rules:
            if all(
                self.match_value(self.get_nested_value(metadata, key), value)
                for key, value in rule.match.items()
            ):
                return rule.prefix
        return config.default_prefix

    def classify_transaction(self, txn, rules=None):
        """Classify a transaction using first-match-wins rules."""
        if rules is None:
            if self.profile is None:
                raise RuntimeError("RuleEngine requires a profile or explicit rules")
            rules = self.profile.reconciliation.rules
        normalized = strip_diacritics(txn.description).upper()
        for rule in rules:
            if rule.direction is not None:
                if rule.direction == "credit" and txn.amount <= 0:
                    continue
                if rule.direction == "debit" and txn.amount > 0:
                    continue
            if rule.match_description and not any(keyword.upper() in normalized for keyword in rule.match_description):
                continue
            return rule.name, rule
        return "unclassified", None

    def parse_cardinality(self, value) -> tuple[int, int | None]:
        """Parse cardinality config into (min, max)."""
        if isinstance(value, int):
            return value, value
        if isinstance(value, list) and len(value) == 2:
            minimum = value[0] if value[0] is not None else 0
            maximum = value[1]
            return int(minimum), int(maximum) if maximum is not None else None
        return 0, None

    def validate_match(self, match, rules=None) -> list[str]:
        """Validate a single reconciliation match."""
        if rules is None:
            if self.profile is None:
                raise RuntimeError("RuleEngine requires a profile or explicit rules")
            rules = self.profile.reconciliation.rules
        category, rule = self.classify_transaction(match.transaction, rules)
        if rule is None:
            return ["unclassified transaction"]

        errors: list[str] = []
        for pattern, cardinality in rule.required_types.items():
            min_count, max_count = self.parse_cardinality(cardinality)
            count = sum(
                1
                for candidate in match.pdf_candidates
                if self.candidate_doc_type(candidate)
                and self.match_doc_type(self.candidate_doc_type(candidate), pattern)
            )
            display_pattern = pattern.replace("|", "/")
            if count < min_count:
                errors.append(f"missing {display_pattern} (expected {min_count}, found {count})")
            elif max_count is not None and count > max_count:
                errors.append(
                    f"too many {display_pattern} (expected max {max_count}, found {count})"
                )

        all_patterns = list(rule.required_types.keys()) + list(rule.shared_types.keys())
        for candidate in match.pdf_candidates:
            candidate_doc_type = self.candidate_doc_type(candidate)
            if candidate_doc_type is None:
                errors.append(f"unexpected document with unknown type ({candidate.pdf_filename})")
            elif not any(self.match_doc_type(candidate_doc_type, pattern) for pattern in all_patterns):
                errors.append(f"unexpected {candidate.document_type} ({candidate.pdf_filename})")

        if rule.expected_page_count:
            for candidate in match.pdf_candidates:
                candidate_doc_type = self.candidate_doc_type(candidate)
                if candidate_doc_type and candidate.page_count is not None:
                    for pattern, expected in rule.expected_page_count.items():
                        expected_counts = expected if isinstance(expected, list) else [expected]
                        if self.match_doc_type(candidate_doc_type, pattern) and candidate.page_count not in expected_counts:
                            expected_text = "/".join(str(count) for count in expected_counts)
                            errors.append(
                                f"{candidate_doc_type} has {candidate.page_count} pages (expected {expected_text})"
                            )

        return errors

    def select_merge_pairs(self, match, merge_rules=None) -> list[tuple[object, object]]:
        """Select target and attachment candidates for merge rules."""
        if merge_rules is None:
            if self.profile is None:
                raise RuntimeError("RuleEngine requires a profile or explicit merge_rules")
            merge_rules = self.profile.export.merge_rules
        pairs: list[tuple[object, object]] = []
        for rule in merge_rules:
            targets = [
                candidate
                for candidate in match.pdf_candidates
                if self.candidate_doc_type(candidate)
                and self.match_doc_type(self.candidate_doc_type(candidate), rule.target_type)
                and not candidate.is_sub_document
            ]
            attachments = [
                candidate
                for candidate in match.pdf_candidates
                if self.candidate_doc_type(candidate)
                and self.match_doc_type(self.candidate_doc_type(candidate), rule.attach_type)
                and not candidate.is_sub_document
            ]
            for target in targets:
                for attachment in attachments:
                    if target != attachment:
                        pairs.append((target, attachment))
        return pairs
