"""Document lifecycle engine and classification helpers."""

from __future__ import annotations

import json
import shutil
import tempfile
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from papertrail.bank_statement.extractor import (
    BankStatementParseError,
    BankStatementReadError,
    classify_bank_statement,
)
from papertrail.document_types import normalize_document_type
from papertrail.hashing import hash_file_content, hash_file_fast, hash_file_text
from papertrail.llm import (
    build_extraction_tools,
    get_qr_exclusions,
    get_system_prompt_classify,
    normalize_issuing_party,
)
from papertrail.logging_utils import DocumentLogger, get_logger, log_failure
from papertrail.models import DocumentMetadata, DocumentMetadataRaw, SubDocumentMetadata
from papertrail.naming import file_name_from_metadata
from papertrail.pdf import (
    convert_image_to_pdf,
    find_document_files,
    get_page_count,
    is_image_file,
    is_splittable_bundle,
    render_pdf_to_images,
    split_pdf_bundle,
)
from papertrail.qr.extractor import configure_zbar_library_paths, extract_all_metadata_from_qr
from papertrail.qr.models import QRExtractedMetadata
from papertrail.repository import DocumentRepository
from papertrail.runtime import Runtime

logger = get_logger("engine")

_QR_MERGE_FIELDS = (
    "issue_date",
    "document_type",
    "total_amount",
    "total_amount_currency",
    "issuer_tax_number",
    "locale",
)


def _merge_qr_metadata(
    qr_metadata: QRExtractedMetadata,
    llm_values: dict,
    doc_logger: DocumentLogger | None,
) -> dict:
    for field_name in _QR_MERGE_FIELDS:
        qr_val = getattr(qr_metadata, field_name)
        if qr_val is not None:
            if doc_logger and llm_values[field_name] != qr_val:
                doc_logger.log_qr_merge(field_name, qr_val, llm_values[field_name])
            llm_values[field_name] = qr_val
    return llm_values


def _extract_qr_with_runtime_settings(
    pdf_path: Path,
    runtime: Runtime,
) -> list[tuple[QRExtractedMetadata, dict]]:
    settings = runtime.profile.processing.qr
    if not settings.enabled:
        return []
    configure_zbar_library_paths(runtime.profile.dependencies.zbar_library_paths)
    return extract_all_metadata_from_qr(
        pdf_path,
        max_pages=settings.max_pages,
        include_last=settings.include_last,
        dpi=settings.dpi,
        currency_by_country=settings.currency_by_country,
        default_currency=settings.default_currency,
        document_type_codes=settings.document_type_codes,
    )


def _phase0_qr_extract(pdf_path: Path, runtime: Runtime, doc_logger: DocumentLogger | None):
    try:
        t0 = _time.monotonic()
        all_results = _extract_qr_with_runtime_settings(pdf_path, runtime)
        if doc_logger:
            doc_logger.log_timing("qr_extraction", _time.monotonic() - t0)

        if not all_results:
            if doc_logger:
                doc_logger.log_qr_not_found()
            return None, None, []

        if len(all_results) == 1:
            qr_metadata, qr_raw_data = all_results[0]
            if doc_logger:
                doc_logger.log_qr_extraction(
                    qr_metadata.extraction_source,
                    {
                        key: getattr(qr_metadata, key)
                        for key in (
                            "issue_date",
                            "document_type",
                            "total_amount",
                            "issuer_nif",
                            "issuer_tax_number",
                            "atcud",
                            "locale",
                        )
                    },
                )
            return qr_metadata, qr_raw_data, []

        if doc_logger:
            doc_logger.log_multi_qr(len(all_results), pdf_path.name)
            for qr_meta, qr_raw in all_results:
                doc_logger.log_qr_extraction(
                    qr_meta.extraction_source,
                    {
                        key: getattr(qr_meta, key)
                        for key in (
                            "issue_date",
                            "document_type",
                            "total_amount",
                            "issuer_nif",
                            "issuer_tax_number",
                            "atcud",
                            "locale",
                        )
                    },
                    page_number=qr_raw.get("page_number", 0) if qr_raw else 0,
                )
        return None, None, all_results
    except Exception as exc:
        logger.debug(f"QR extraction failed (continuing with LLM): {exc}")
        if doc_logger:
            doc_logger.log_qr_not_found()
        return None, None, []


def _phase1_llm_extract(
    pdf_path: Path,
    runtime: Runtime,
    repository: DocumentRepository,
    scope: str | Path,
    exclude_fields: set[str],
    pre_extracted: dict,
    doc_logger: DocumentLogger | None,
    *,
    multi_qr_info: dict | None = None,
) -> DocumentMetadataRaw:
    t0 = _time.monotonic()
    render_settings = runtime.profile.processing.render
    images_b64 = render_pdf_to_images(
        pdf_path,
        max_pages=render_settings.max_pages,
        enhance_contrast=render_settings.enhance_contrast,
        contrast_factor=render_settings.contrast_factor,
    )
    if doc_logger:
        doc_logger.log_timing("pdf_render", _time.monotonic() - t0)

    messages = [
        {
            "role": "system",
            "content": get_system_prompt_classify(
                repository.registry.document_types(scope),
                repository.registry.issuing_parties(scope),
                pre_extracted or None,
                multi_qr_info=multi_qr_info,
                classification_settings=runtime.profile.classification,
            ),
        },
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}}
                for img_b64 in images_b64
            ],
        },
    ]

    t0 = _time.monotonic()
    response = runtime.openai_client.chat.completions.create(
        model=runtime.model_id,
        max_tokens=runtime.profile.openrouter.requests.classification_max_tokens,
        temperature=runtime.profile.openrouter.requests.classification_temperature,
        messages=messages,
        tools=build_extraction_tools(exclude_fields),
        tool_choice={"type": "function", "function": {"name": "extract_document_metadata"}},
    )
    if doc_logger:
        doc_logger.log_timing("llm_extraction", _time.monotonic() - t0)
        if response.usage:
            doc_logger.log_llm_usage(
                runtime.model_id or "",
                response.usage.prompt_tokens,
                response.usage.completion_tokens,
            )

    if response.usage and response.usage.prompt_tokens < 100 and images_b64:
        logger.warning(
            f"[VISION-WARNING] Suspiciously low prompt_tokens={response.usage.prompt_tokens} "
            f"for vision request with {len(images_b64)} images ({pdf_path.name}). "
            f"The API may not be forwarding images — classification may be hallucinated."
        )

    tool_calls = response.choices[0].message.tool_calls
    if not tool_calls:
        raise ValueError("OpenRouter did not return structured classification.")

    args = tool_calls[0].function.arguments
    if pre_extracted:
        args_dict = json.loads(args)
        for field, value in pre_extracted.items():
            if field not in args_dict:
                args_dict[field] = value
        args = json.dumps(args_dict)

    raw_metadata = DocumentMetadataRaw.model_validate_json(args)
    if doc_logger:
        doc_logger.log_extraction(raw_metadata.model_dump())
    return raw_metadata


def _enrich_nif(
    tax_number: str | None,
    runtime: Runtime,
    repository: DocumentRepository,
    scope: str | Path,
    doc_logger: DocumentLogger | None,
) -> tuple[str | None, str | None]:
    if not (
        tax_number
        and runtime.nif_cache
        and runtime.nif_cache.is_supported_nif(tax_number)
    ):
        return None, None

    official_issuer, lookup_source, lookup_error = runtime.nif_cache.lookup(tax_number)
    if not official_issuer:
        if doc_logger:
            if lookup_source == "web_error" and lookup_error:
                doc_logger.log_nif_web_error(tax_number, lookup_error)
            else:
                doc_logger.log_nif_not_found(tax_number, lookup_source)
        return None, None

    if doc_logger:
        if lookup_source == "cache":
            doc_logger.log_nif_cache_hit(tax_number, official_issuer)
        elif lookup_source == "web":
            doc_logger.log_nif_web_lookup(tax_number, official_issuer)

    cached_normalized = runtime.nif_cache.get_normalized(tax_number)
    if cached_normalized:
        if doc_logger:
            doc_logger.log_nif_enrichment(tax_number, official_issuer, cached_normalized)
        repository.registry.register_issuing_party(cached_normalized)
        return cached_normalized, official_issuer

    nif_normalized = normalize_issuing_party(
        official_issuer,
        runtime.openai_client,
        runtime.model_id,
        repository.registry.issuing_parties(scope),
        max_tokens=runtime.profile.openrouter.requests.normalization_max_tokens,
        temperature=runtime.profile.openrouter.requests.normalization_temperature,
        legal_suffixes=runtime.profile.classification.legal_suffixes,
    )
    if nif_normalized != "$UNKNOWN$":
        canonical = repository.registry.register_issuing_party(nif_normalized)
        runtime.nif_cache.set_normalized(tax_number, canonical or nif_normalized)
        if doc_logger:
            doc_logger.log_nif_enrichment(tax_number, official_issuer, canonical or nif_normalized)
        return canonical or nif_normalized, official_issuer

    logger.debug(f"[NIF-ENRICH] Keeping original issuer (NIF name didn't normalize): {official_issuer}")
    return None, official_issuer


def _phase4_nif_enrich(
    merged: dict,
    normalized_issuing_party: str,
    runtime: Runtime,
    repository: DocumentRepository,
    scope: str | Path,
    doc_logger: DocumentLogger | None,
) -> str:
    enabled_locales = set(getattr(runtime.profile.nif_api, "enabled_locales", ["pt-PT"]) or [])
    if enabled_locales and merged["locale"] not in enabled_locales:
        return normalized_issuing_party

    t0 = _time.monotonic()
    enriched, _ = _enrich_nif(
        merged["issuer_tax_number"],
        runtime,
        repository,
        scope,
        doc_logger,
    )
    if enriched:
        normalized_issuing_party = enriched
    if doc_logger:
        doc_logger.log_timing("nif_enrichment", _time.monotonic() - t0)
    if runtime.nif_cache is not None:
        runtime.nif_cache.save()
    return normalized_issuing_party


def _build_sub_documents(
    all_qr_results: list[tuple[QRExtractedMetadata, dict]],
    runtime: Runtime,
    repository: DocumentRepository,
    scope: str | Path,
    doc_logger: DocumentLogger | None,
) -> list[dict]:
    sub_docs = []
    for qr_metadata, qr_raw_data in all_qr_results:
        enriched, raw_issuer = _enrich_nif(
            qr_metadata.issuer_tax_number,
            runtime,
            repository,
            scope,
            doc_logger,
        )
        if enriched:
            repository.registry.register_issuing_party(enriched)
        sub_doc = SubDocumentMetadata(
            date_issued=qr_metadata.issue_date,
            document_type=repository.registry.canonicalize_document_type(qr_metadata.document_type),
            total_amount=qr_metadata.total_amount,
            total_amount_currency=qr_metadata.total_amount_currency,
            issuer_tax_number=qr_metadata.issuer_tax_number,
            issuing_party=enriched,
            issuing_party_raw=raw_issuer,
            document_number=qr_metadata.document_number,
            atcud=qr_metadata.atcud,
            locale=qr_metadata.locale,
            qrcode=qr_raw_data,
        )
        sub_docs.append(sub_doc.model_dump())

    if runtime.nif_cache is not None:
        runtime.nif_cache.save()
    return sub_docs


def _collect_sync_targets(
    repository: DocumentRepository,
    processed_path: Path,
    *,
    all_unknown: bool = False,
    pattern: str | None = None,
    orphans_only: bool = False,
) -> list[tuple[Path, Path, dict | None]]:
    from papertrail.utils import make_matcher

    console = repository.runtime.console
    pdf_files = [
        path
        for path in processed_path.rglob("*.pdf")
        if not repository.is_internal_path(path.relative_to(processed_path))
    ]
    if not pdf_files:
        logger.debug(f"No PDF files found in {processed_path}")
        return []

    targets: list[tuple[Path, Path, dict | None]] = []

    if pattern:
        target_pdf = processed_path / pattern
        if target_pdf.exists() and target_pdf.suffix.lower() == ".pdf":
            target_json = target_pdf.with_suffix(".json")
            data = None
            if target_json.exists():
                if orphans_only:
                    return targets
                data = repository.load_metadata(target_json)
            targets.append((target_json, target_pdf, data))
        else:
            matcher = make_matcher(pattern)
            for pdf_path in console.track(pdf_files, "Matching pattern"):
                if not matcher(pdf_path.name):
                    continue
                metadata_path = pdf_path.with_suffix(".json")
                has_metadata = metadata_path.exists()
                if orphans_only and has_metadata:
                    continue
                data = None
                if has_metadata:
                    try:
                        data = repository.load_metadata(metadata_path)
                    except Exception as exc:
                        logger.warning(f"Failed to load {metadata_path.name}: {exc}")
                targets.append((metadata_path, pdf_path, data))
    elif orphans_only:
        for pdf_path in console.track(pdf_files, "Scanning for orphans"):
            metadata_path = pdf_path.with_suffix(".json")
            if not metadata_path.exists():
                targets.append((metadata_path, pdf_path, None))

    if all_unknown:
        for metadata_path, data in console.track(
            list(repository.iter_sidecars(processed_path)),
            "Scanning for $UNKNOWN$",
        ):
            try:
                has_unknown = (
                    data.get("document_type") == "$UNKNOWN$"
                    or data.get("issuing_party") == "$UNKNOWN$"
                    or data.get("date_issued") == "$UNKNOWN$"
                )
                if has_unknown:
                    pdf_path = metadata_path.with_suffix(".pdf")
                    if pdf_path.exists():
                        targets.append((metadata_path, pdf_path, data))
                    else:
                        logger.warning(f"PDF not found for {metadata_path.name}")
            except Exception as exc:
                logger.warning(f"Skipping {metadata_path.name}: {exc}")

    targets.sort(key=lambda item: item[1].name, reverse=True)
    return targets


@dataclass
class UpsertResult:
    source_path: Path
    mode: str
    outputs: list[Path] = field(default_factory=list)
    processed: int = 0
    duplicates: int = 0
    batch_duplicates: int = 0
    skipped: int = 0
    failed: int = 0
    images_converted: int = 0
    bundles_split: int = 0
    split_pages: int = 0
    reason: str | None = None
    metadata: DocumentMetadata | None = None

    def absorb(self, other: "UpsertResult") -> "UpsertResult":
        self.outputs.extend(other.outputs)
        self.processed += other.processed
        self.duplicates += other.duplicates
        self.batch_duplicates += other.batch_duplicates
        self.skipped += other.skipped
        self.failed += other.failed
        self.images_converted += other.images_converted
        self.bundles_split += other.bundles_split
        self.split_pages += other.split_pages
        if self.reason is None:
            self.reason = other.reason
        return self


class DocumentEngine:
    """Canonical document lifecycle engine."""

    def __init__(self, runtime: Runtime, repository: DocumentRepository | None = None):
        self.runtime = runtime
        self.repository = repository or DocumentRepository(runtime)

    def classify_pdf_document(
        self,
        pdf_path: Path,
        file_hash: str,
        *,
        failure_logger=None,
        doc_logger: DocumentLogger | None = None,
        scope: str | Path = "processed",
    ) -> DocumentMetadata:
        if doc_logger:
            doc_logger.start_document(pdf_path)

        qr_metadata, qr_raw_data, all_qr_results = _phase0_qr_extract(
            pdf_path,
            self.runtime,
            doc_logger,
        )
        is_multi_qr = len(all_qr_results) >= 2

        exclude_fields, pre_extracted = set(), {}
        if qr_metadata and not is_multi_qr:
            exclude_fields, pre_extracted = get_qr_exclusions(qr_metadata)
            if doc_logger and exclude_fields:
                doc_logger.log_qr_skip(exclude_fields)

        multi_qr_info = None
        if is_multi_qr:
            issuers = []
            for qr_meta, _ in all_qr_results:
                name = qr_meta.issuer_nif or qr_meta.issuer_tax_number
                if name and name not in issuers:
                    issuers.append(name)
            multi_qr_info = {"count": len(all_qr_results), "issuers": issuers}

        try:
            raw_metadata = None
            max_attempts = max(
                1,
                int(getattr(self.runtime.profile.openrouter.requests, "classification_retries", 2) or 2),
            )
            for attempt in range(max_attempts):
                try:
                    raw_metadata = _phase1_llm_extract(
                        pdf_path,
                        self.runtime,
                        self.repository,
                        scope,
                        exclude_fields,
                        pre_extracted,
                        doc_logger,
                        multi_qr_info=multi_qr_info,
                    )
                    break
                except Exception as exc:
                    if attempt < max_attempts - 1:
                        logger.warning(
                            f"LLM classification attempt {attempt + 1} failed for {pdf_path.name}, "
                            f"retrying: {exc}"
                        )
                    else:
                        raise RuntimeError(
                            f"LLM classification failed after {max_attempts} attempts for "
                            f"{pdf_path.name}: {exc}"
                        ) from exc

            assert raw_metadata is not None

            sub_documents = (
                _build_sub_documents(
                    all_qr_results,
                    self.runtime,
                    self.repository,
                    scope,
                    doc_logger,
                )
                if is_multi_qr
                else None
            )

            merged = {
                "issue_date": raw_metadata.issue_date,
                "document_type": self.repository.registry.canonicalize_document_type(
                    normalize_document_type(
                        raw_metadata.document_type,
                        raw_metadata.document_type_raw,
                        raw_metadata.document_title,
                        self.runtime.profile.classification.document_type_overrides,
                    )
                ),
                "total_amount": raw_metadata.total_amount,
                "total_amount_currency": raw_metadata.total_amount_currency,
                "issuer_tax_number": raw_metadata.issuer_tax_number,
                "locale": raw_metadata.locale,
            }
            if qr_metadata and not is_multi_qr:
                _merge_qr_metadata(qr_metadata, merged, doc_logger)
                merged["document_type"] = self.repository.registry.canonicalize_document_type(merged["document_type"])

            if merged["document_type"] != "$UNKNOWN$":
                self.repository.registry.register_document_type(merged["document_type"])

            normalized_issuing_party = self.repository.registry.canonicalize_issuing_party(raw_metadata.issuing_party)
            normalized_issuing_party = _phase4_nif_enrich(
                merged,
                normalized_issuing_party or "$UNKNOWN$",
                self.runtime,
                self.repository,
                scope,
                doc_logger,
            )
            if normalized_issuing_party != "$UNKNOWN$":
                normalized_issuing_party = (
                    self.repository.registry.register_issuing_party(normalized_issuing_party)
                    or normalized_issuing_party
                )

            metadata = DocumentMetadata(
                date_issued=merged["issue_date"],
                document_type=merged["document_type"] or "$UNKNOWN$",
                issuing_party=normalized_issuing_party or "$UNKNOWN$",
                total_amount=merged["total_amount"],
                total_amount_currency=merged["total_amount_currency"],
                class_confidence=(
                    raw_metadata.confidence if is_multi_qr else (1.0 if qr_metadata else raw_metadata.confidence)
                ),
                class_reasoning=raw_metadata.reasoning,
                hash_content=file_hash,
                document_type_raw=raw_metadata.document_type_raw,
                document_title=raw_metadata.document_title,
                issuing_party_raw=raw_metadata.issuing_party_raw,
                issuer_tax_number=merged["issuer_tax_number"],
                locale=merged["locale"],
                qrcode=None if is_multi_qr else qr_raw_data,
                sub_documents=sub_documents,
            )
            metadata.date_created = self.runtime.today
            metadata.date_updated = self.runtime.today
            metadata.page_count = get_page_count(pdf_path)

            if doc_logger:
                doc_logger.log_final(metadata.model_dump())
                doc_logger.end_document("SUCCESS")
            return metadata
        except Exception as exc:
            log_failure(failure_logger, pdf_path, exc)
            if doc_logger:
                doc_logger.end_document("FAILED")
            raise RuntimeError(f"Classification failed for: {pdf_path}") from exc

    def _load_known_hashes(
        self,
        processed_path: Path,
        known_file_hashes: set[str] | None,
        known_content_hashes: set[str] | None,
        known_text_hashes: set[str] | None,
    ) -> tuple[set[str], set[str], set[str]]:
        if (
            known_file_hashes is not None
            and known_content_hashes is not None
            and known_text_hashes is not None
        ):
            return known_file_hashes, known_content_hashes, known_text_hashes

        content_idx, file_idx, text_idx, _ = self.repository.build_indexes(processed_path)
        return (
            known_file_hashes if known_file_hashes is not None else set(file_idx.keys()),
            known_content_hashes if known_content_hashes is not None else set(content_idx.keys()),
            known_text_hashes if known_text_hashes is not None else set(text_idx.keys()),
        )

    def _complete_upsert(
        self,
        result: UpsertResult,
        source_path: Path,
        metadata: DocumentMetadata,
        *,
        processed_path: Path,
        mode: str,
        file_hash: str,
        content_hash: str,
        known_file_hashes: set[str],
        known_content_hashes: set[str],
        known_text_hashes: set[str] | None = None,
        text_hash: str | None = None,
        rename_on_resync: bool = False,
        dry_run: bool,
    ) -> UpsertResult:
        dest_path = source_path
        naming_settings = self.runtime.profile.naming
        if mode == "ingest":
            dest_path = processed_path / file_name_from_metadata(
                metadata,
                file_hash,
                component_max_chars=naming_settings.component_max_chars,
            )
            if dest_path.exists():
                result.skipped = 1
                result.reason = "destination_exists"
                return result

        if not dry_run:
            if mode == "ingest":
                shutil.copy2(source_path, dest_path)
            self.repository.save_document(dest_path, metadata)
            if rename_on_resync and mode == "resync":
                new_doc_path = source_path.parent / file_name_from_metadata(
                    metadata,
                    metadata.hash_file,
                    component_max_chars=naming_settings.component_max_chars,
                )
                if new_doc_path != source_path:
                    source_path.rename(new_doc_path)
                    source_path.with_suffix(".json").rename(new_doc_path.with_suffix(".json"))
                    dest_path = new_doc_path

        known_file_hashes.add(file_hash)
        known_content_hashes.add(content_hash)
        if text_hash and known_text_hashes is not None:
            known_text_hashes.add(text_hash)
        result.processed = 1
        result.outputs.append(dest_path)
        result.metadata = metadata
        return result

    def upsert(
        self,
        source_path: Path,
        mode: str,
        existing_metadata=None,
        *,
        processed_path: Path | None = None,
        known_file_hashes: set[str] | None = None,
        known_content_hashes: set[str] | None = None,
        known_text_hashes: set[str] | None = None,
        failure_logger=None,
        doc_logger: DocumentLogger | None = None,
        dry_run: bool = False,
    ) -> UpsertResult:
        processed_path = processed_path or self.runtime.require_processed_path()
        result = UpsertResult(source_path=source_path, mode=mode)

        if mode not in {"ingest", "resync", "backfill"}:
            raise ValueError(f"Unsupported upsert mode: {mode}")

        if mode == "backfill":
            data = existing_metadata
            if isinstance(data, DocumentMetadata):
                data = data.model_dump()
            if data is None:
                data = {}

            changed = False
            if data.get("page_count") is None and source_path.exists() and source_path.suffix.lower() == ".pdf":
                data["page_count"] = get_page_count(source_path)
                changed = True
            if data.get("file_size_kb") is None and source_path.exists():
                data["file_size_kb"] = round(source_path.stat().st_size / 1024)
                changed = True
            if "hash_text" not in data and source_path.exists():
                hashing_settings = self.runtime.profile.processing.hashing
                data["hash_text"] = (
                    hash_file_text(source_path, min_chars=hashing_settings.text_min_chars)
                    if source_path.suffix.lower() == ".pdf"
                    else None
                )
                changed = True
            if data.get("sub_documents") is None and "sub_documents" not in data:
                if source_path.exists() and source_path.suffix.lower() == ".pdf":
                    all_results = _extract_qr_with_runtime_settings(source_path, self.runtime)
                    if len(all_results) >= 2:
                        data["sub_documents"] = _build_sub_documents(
                            all_results,
                            self.runtime,
                            self.repository,
                            processed_path,
                            None,
                        )
                        data["qrcode"] = None
                    else:
                        data["sub_documents"] = None
                    changed = True
                elif source_path.exists():
                    data["sub_documents"] = None
                    changed = True

            if changed:
                data["date_updated"] = self.runtime.today
                if not dry_run:
                    self.repository.save_json(source_path.with_suffix(".json"), data)
                result.processed = 1
                result.outputs.append(source_path)
            else:
                result.skipped = 1
            return result

        known_file_hashes, known_content_hashes, known_text_hashes = self._load_known_hashes(
            processed_path,
            known_file_hashes,
            known_content_hashes,
            known_text_hashes,
        )

        suffix = source_path.suffix.lower()
        input_settings = self.runtime.profile.processing.input
        if mode == "ingest" and is_image_file(
            source_path,
            image_extensions=input_settings.image_extensions,
        ):
            with tempfile.TemporaryDirectory() as tmp_dir:
                converted_path = convert_image_to_pdf(source_path, Path(tmp_dir))
                result.images_converted = 1
                return result.absorb(
                    self.upsert(
                        converted_path,
                        "ingest",
                        processed_path=processed_path,
                        known_file_hashes=known_file_hashes,
                        known_content_hashes=known_content_hashes,
                        known_text_hashes=known_text_hashes,
                        failure_logger=failure_logger,
                        doc_logger=doc_logger,
                        dry_run=dry_run,
                    )
                )

        bundle_settings = self.runtime.profile.processing.bundle
        if mode == "ingest" and suffix == ".pdf" and is_splittable_bundle(
            source_path,
            enabled=bundle_settings.enabled,
            pagination_patterns=bundle_settings.pagination_patterns,
        ):
            with tempfile.TemporaryDirectory() as tmp_dir:
                split_paths = split_pdf_bundle(source_path, Path(tmp_dir))
                result.bundles_split = 1
                result.split_pages = len(split_paths)
                for split_path in split_paths:
                    result.absorb(
                        self.upsert(
                            split_path,
                            "ingest",
                            processed_path=processed_path,
                            known_file_hashes=known_file_hashes,
                            known_content_hashes=known_content_hashes,
                            known_text_hashes=known_text_hashes,
                            failure_logger=failure_logger,
                            doc_logger=doc_logger,
                            dry_run=dry_run,
                        )
                    )
                return result

        if suffix == ".xlsx":
            hashing_settings = self.runtime.profile.processing.hashing
            file_hash = hash_file_fast(
                source_path,
                chunk_size=hashing_settings.fast_chunk_size,
            )
            if mode == "ingest" and file_hash in known_file_hashes:
                result.duplicates = 1
                result.reason = "hash_file"
                return result

            try:
                metadata = classify_bank_statement(
                    source_path,
                    file_hash,
                    locale=self.runtime.profile.classification.bank_statement_locale,
                    settings=self.runtime.profile.bank_statements,
                )
            except BankStatementReadError as exc:
                logger.error(f"Failed to read XLSX {source_path.name}: {exc}")
                result.failed = 1
                result.reason = "unreadable_xlsx"
                return result
            except BankStatementParseError as exc:
                logger.error(f"Failed to parse XLSX {source_path.name}: {exc}")
                result.failed = 1
                result.reason = "parse_failed_xlsx"
                return result
            if metadata is None:
                result.skipped = 1
                result.reason = "unrecognized_xlsx"
                return result

            metadata.file_size_kb = round(source_path.stat().st_size / 1024)
            if mode == "resync" and existing_metadata:
                old_data = existing_metadata.model_dump() if isinstance(existing_metadata, DocumentMetadata) else existing_metadata
                metadata.date_created = old_data.get("date_created") or self.runtime.today
                metadata.date_updated = self.runtime.today

            return self._complete_upsert(
                result,
                source_path,
                metadata,
                processed_path=processed_path,
                mode=mode,
                file_hash=file_hash,
                content_hash=file_hash,
                known_file_hashes=known_file_hashes,
                known_content_hashes=known_content_hashes,
                dry_run=dry_run,
            )

        if suffix != ".pdf":
            result.skipped = 1
            result.reason = "unsupported_extension"
            return result

        old_data = None
        if existing_metadata:
            old_data = existing_metadata.model_dump() if isinstance(existing_metadata, DocumentMetadata) else existing_metadata

        hashing_settings = self.runtime.profile.processing.hashing
        file_hash = old_data.get("hash_file") if old_data else None
        if not file_hash:
            file_hash = hash_file_fast(
                source_path,
                chunk_size=hashing_settings.fast_chunk_size,
            )
        if mode == "ingest" and file_hash in known_file_hashes:
            result.duplicates = 1
            result.reason = "hash_file"
            return result

        text_hash = hash_file_text(source_path, min_chars=hashing_settings.text_min_chars)
        if mode == "ingest" and text_hash and text_hash in known_text_hashes:
            result.duplicates = 1
            result.reason = "hash_text"
            return result

        content_hash = old_data.get("hash_content") if old_data else None
        if not content_hash:
            content_hash = hash_file_content(
                source_path,
                dpi=hashing_settings.content_dpi,
            )
        if mode == "ingest" and content_hash in known_content_hashes:
            result.duplicates = 1
            result.reason = "hash_content"
            return result

        metadata = self.classify_pdf_document(
            source_path,
            content_hash,
            failure_logger=failure_logger,
            doc_logger=doc_logger,
            scope=processed_path,
        )
        metadata.hash_file = file_hash
        metadata.hash_text = text_hash
        metadata.file_size_kb = round(source_path.stat().st_size / 1024)
        if old_data:
            metadata.date_created = old_data.get("date_created") or self.runtime.today
            metadata.date_updated = self.runtime.today

        return self._complete_upsert(
            result,
            source_path,
            metadata,
            processed_path=processed_path,
            mode=mode,
            file_hash=file_hash,
            content_hash=content_hash,
            known_file_hashes=known_file_hashes,
            known_content_hashes=known_content_hashes,
            known_text_hashes=known_text_hashes,
            text_hash=text_hash,
            rename_on_resync=True,
            dry_run=dry_run,
        )

    def extract(
        self,
        processed_path: Path,
        raw_paths: list[Path],
        *,
        quiet: bool = False,
        failure_logger=None,
    ) -> dict:
        console = self.runtime.console
        doc_logger = DocumentLogger()
        run_start = _time.monotonic()

        logger.debug("Building hash index from metadata files...")
        known_content_hashes_idx, known_file_hashes_idx, known_text_hashes_idx, known_issuers_before = self.repository.build_indexes(processed_path)
        known_content_hashes = set(known_content_hashes_idx.keys())
        known_file_hashes = set(known_file_hashes_idx.keys())
        known_text_hashes = set(known_text_hashes_idx.keys())
        hashes_before = set(known_content_hashes)

        input_settings = self.runtime.profile.processing.input
        all_doc_paths = find_document_files(
            raw_paths,
            extensions=input_settings.extensions,
            skip_dirs=input_settings.skip_dirs,
            skip_dir_prefixes=input_settings.skip_dir_prefixes,
            skip_hidden_files=input_settings.skip_hidden_files,
        )
        pdf_scanned = sum(1 for path in all_doc_paths if path.suffix.lower() == ".pdf")
        xlsx_scanned = sum(1 for path in all_doc_paths if path.suffix.lower() == ".xlsx")
        image_scanned = sum(
            1
            for path in all_doc_paths
            if is_image_file(path, image_extensions=input_settings.image_extensions)
        )
        logger.debug(
            f"Found {pdf_scanned} PDFs, {xlsx_scanned} XLSX, and "
            f"{image_scanned} images in raw directories"
        )

        totals = {
            "new": 0,
            "duplicates": 0,
            "batch_duplicates": 0,
            "failed": 0,
            "xlsx_new": 0,
            "pdf_new": 0,
            "images_converted": 0,
            "bundles_split": 0,
            "split_pages": 0,
        }

        for doc_path in console.track(all_doc_paths, "Processing documents"):
            try:
                upsert = self.upsert(
                    doc_path,
                    "ingest",
                    processed_path=processed_path,
                    known_file_hashes=known_file_hashes,
                    known_content_hashes=known_content_hashes,
                    known_text_hashes=known_text_hashes,
                    failure_logger=failure_logger,
                    doc_logger=doc_logger,
                )
            except Exception as exc:
                logger.error(f"Failed to process {doc_path.name}: {exc}")
                totals["failed"] += 1
                continue
            totals["new"] += upsert.processed
            totals["duplicates"] += upsert.duplicates
            totals["batch_duplicates"] += upsert.batch_duplicates
            totals["failed"] += upsert.failed
            totals["images_converted"] += upsert.images_converted
            totals["bundles_split"] += upsert.bundles_split
            totals["split_pages"] += upsert.split_pages
            if upsert.processed > 0:
                if doc_path.suffix.lower() == ".xlsx":
                    totals["xlsx_new"] += upsert.processed
                else:
                    totals["pdf_new"] += upsert.processed

        elapsed = _time.monotonic() - run_start

        new_hashes = known_content_hashes - hashes_before
        new_issuers: set[str] = set()
        unknown_dt_count = 0
        unknown_ip_count = 0
        if new_hashes:
            for _, data in self.repository.iter_sidecars(processed_path):
                content_hash = data.get("hash_content")
                if content_hash not in new_hashes:
                    continue
                issuing_party = data.get("issuing_party")
                if issuing_party and issuing_party != "$UNKNOWN$" and issuing_party not in known_issuers_before:
                    new_issuers.add(issuing_party)
                if data.get("document_type") == "$UNKNOWN$":
                    unknown_dt_count += 1
                if issuing_party == "$UNKNOWN$":
                    unknown_ip_count += 1

        if not quiet and (pdf_scanned > 0 or xlsx_scanned > 0):
            console.success(
                f"{pdf_scanned} PDFs scanned, {totals['pdf_new']} processed successfully",
                indent=False,
            )
            if totals["xlsx_new"] > 0:
                console.success(f"{totals['xlsx_new']} XLSX file(s) processed", indent=False)
            if totals["failed"] > 0:
                console.warning(f"{totals['new']} processed, {totals['failed']} failed", indent=False)

        logger.debug(
            f"=== SUMMARY: {totals['new']} success, {totals['failed']} failed, "
            f"{elapsed:.1f}s total ==="
        )

        return {
            "pdf_scanned": pdf_scanned,
            "xlsx_scanned": xlsx_scanned,
            "new": totals["new"],
            "duplicates": totals["duplicates"],
            "batch_duplicates": totals["batch_duplicates"],
            "failed": totals["failed"],
            "xlsx_new": totals["xlsx_new"],
            "pdf_new": totals["pdf_new"],
            "images_converted": totals["images_converted"],
            "bundles_split": totals["bundles_split"],
            "split_pages": totals["split_pages"],
            "new_issuers": sorted(new_issuers),
            "unknown_document_type": unknown_dt_count,
            "unknown_issuing_party": unknown_ip_count,
        }

    def sync(
        self,
        processed_path: Path,
        *,
        dry_run: bool = False,
        all_unknown: bool = False,
        pattern: str | None = None,
        workers: int = 1,
        all: bool = False,
        quiet: bool = False,
    ) -> dict:
        console = self.runtime.console
        orphans_only = not all and not all_unknown and pattern is None
        targets = _collect_sync_targets(
            self.repository,
            processed_path,
            all_unknown=all_unknown,
            pattern=pattern,
            orphans_only=orphans_only,
        )
        if not targets:
            if not quiet:
                console.warning("No files to sync", indent=False)
            return {"targets": 0}

        fixed_count = 0
        still_unknown_count = 0
        failed_count = 0
        new_count = 0
        renamed_count = 0

        def classify_one(item):
            metadata_path, pdf_path, old_data = item
            thread_engine = DocumentEngine(self.runtime, self.repository)
            thread_doc_logger = DocumentLogger()
            try:
                upsert = thread_engine.upsert(
                    pdf_path,
                    "resync",
                    existing_metadata=old_data,
                    processed_path=processed_path,
                    doc_logger=thread_doc_logger,
                    dry_run=dry_run,
                )
                if upsert.processed == 0:
                    return metadata_path, old_data, upsert.metadata, None, pdf_path
                output_path = upsert.outputs[0] if upsert.outputs else pdf_path
                return metadata_path, old_data, upsert.metadata, None, output_path
            except Exception as exc:
                return metadata_path, old_data, None, str(exc), pdf_path

        def process_result(metadata_path, old_data, new_metadata, error, output_path):
            nonlocal fixed_count, still_unknown_count, failed_count, new_count, renamed_count
            if error:
                logger.error(f"Failed {metadata_path.name}: {error}")
                failed_count += 1
                return

            if old_data is None:
                new_count += 1
            elif new_metadata is not None:
                changes = []
                if old_data.get("document_type", "") != new_metadata.document_type:
                    changes.append("document_type")
                if old_data.get("issuing_party", "") != new_metadata.issuing_party:
                    changes.append("issuing_party")
                if old_data.get("date_issued", "") != new_metadata.date_issued:
                    changes.append("date_issued")
                if changes:
                    fixed_count += 1
                else:
                    still_unknown_count += 1
            else:
                still_unknown_count += 1

            if output_path != metadata_path.with_suffix(".pdf"):
                renamed_count += 1

        def _truncated_name(path: Path) -> str:
            name = path.stem
            return name[:37] + "..." if len(name) > 40 else name

        if workers == 1:
            for index, item in enumerate(targets, 1):
                logger.debug(f"Syncing [{index}/{len(targets)}] {_truncated_name(item[1])}")
                process_result(*classify_one(item))
        else:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(classify_one, item): item for item in targets}
                with console.progress("Syncing", total=len(futures)) as progress:
                    task = progress.add_task("Syncing", total=len(futures))
                    for future in as_completed(futures):
                        item = futures[future]
                        progress.update(task, description=f"[dim]{_truncated_name(item[1])}[/dim]")
                        process_result(*future.result())
                        progress.update(task, advance=1)

        if not quiet:
            parts = []
            if new_count > 0:
                parts.append(f"{new_count} new")
            if fixed_count > 0:
                parts.append(f"{fixed_count} changed")
            if renamed_count > 0:
                parts.append(f"{renamed_count} renamed")
            if still_unknown_count > 0:
                parts.append(f"{still_unknown_count} unchanged")
            if failed_count > 0:
                parts.append(f"{failed_count} failed")
            summary = ", ".join(parts) if parts else "No files processed"
            if failed_count > 0:
                console.warning(summary, indent=False)
            elif new_count > 0 or fixed_count > 0:
                console.success(summary, indent=False)
            else:
                console.info(f"No changes ({still_unknown_count} files checked)", indent=False)
            if dry_run:
                console.detail("(dry run - no files were modified)", indent=False)

        logger.debug(
            f"New: {new_count}, Changed: {fixed_count}, Renamed: {renamed_count}, "
            f"Unchanged: {still_unknown_count}, Failed: {failed_count}"
        )
        return {
            "targets": len(targets),
            "new": new_count,
            "changed": fixed_count,
            "renamed": renamed_count,
            "unchanged": still_unknown_count,
            "failed": failed_count,
        }

    def backfill_processed(self, processed_path: Path, *, dry_run: bool = False) -> dict:
        updated = 0
        skipped = 0
        errors = 0
        for metadata_path, doc_path, data in self.repository.iter_documents(
            processed_path,
            validate=False,
            require_companion=False,
            show_progress=True,
            progress_desc="Checking metadata",
        ):
            try:
                result = self.upsert(
                    doc_path,
                    "backfill",
                    existing_metadata=data,
                    processed_path=processed_path,
                    dry_run=dry_run,
                )
                if result.processed > 0:
                    updated += 1
                else:
                    skipped += 1
            except Exception as exc:
                logger.error(f"Failed to process {metadata_path.name}: {exc}")
                errors += 1
        return {"updated": updated, "skipped": skipped, "errors": errors}


__all__ = ["DocumentEngine", "UpsertResult"]
