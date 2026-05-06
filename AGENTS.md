# papertrail - Claude Code Context

AI-powered document classification and organization tool using vision LLMs via OpenRouter. Supports PDF documents (LLM-classified), XLSX bank statements (deterministically classified), and image files (PNG, JPG, TIFF, BMP, WebP — converted to PDF on-the-fly).

## Quick Reference

**Run**: `python main.py [--profile NAME] <command> [options]`
**Install**: `uv pip install -e .`
**Check hashes**: `python scripts/check_hash.py <pdf_path>`
**Profile docs**: `profiles/README.md` - Multi-environment configuration system

## Invariants (DO NOT VIOLATE)

These are core constraints that must be preserved in all changes:

1. **Raw value preservation**: Original extracted text MUST be stored in `*_raw` fields (`document_type_raw`, `issuing_party_raw`). This enables re-normalization when needed.

2. **Content hash for dedup, file hash for filenames**: Duplicate detection uses `hash_content` (rendered pixels), not `hash_file` (raw bytes). Two PDFs with identical visual content are duplicates even if their bytes differ. However, **filenames** use `hash_file` (raw bytes) so that every distinct file gets a unique name — even if two files are visual duplicates. Exception: XLSX files use `hash_file_fast` for both hashes (no pixel rendering possible).

3. **`$UNKNOWN$` is the only fallback**: Unrecognized values become `$UNKNOWN$`, never empty string, `null`, or made-up values. This sentinel is used for filtering and re-processing.

4. **QR overrides LLM**: When QR extraction succeeds, those fields have 100% confidence and MUST override LLM-extracted values in the merge phase.

5. **Sidecar JSON is authoritative**: The `.json` file is the source of truth for metadata. The filename is derived from it, not vice versa. If they disagree, `rename` fixes the filename.

## Architecture

### Single-Call Classification Pipeline
1. **Phase 0 - QR Extraction** (optional): Scans PDF for QR codes, extracts metadata with 100% confidence (e.g., Portuguese invoice QR codes)
2. **Phase 1 - Classify** (`classify_pdf_document`): Single LLM vision call that extracts raw text AND normalizes to canonical forms in one step. Renders first 2 pages as JPEG, sends to LLM with the full list of known types/parties. Returns both raw fields (`document_type_raw`, `issuing_party_raw`) and normalized fields (`document_type`, `issuing_party`). For multi-QR PDFs, the LLM prompt includes context about sub-document count and issuer NIFs.
3. **Phase 2 - Merge**: QR-extracted values override LLM values (QR is 100% accurate)
4. **Phase 3 - NIF Enrichment** (optional): If tax number present, looks up official issuer name via nif.pt web scraping. Uses a lightweight `normalize_issuing_party()` LLM call (rare — only first encounter of each NIF) to normalize the official name to slug form.

New values are auto-accepted. A batch summary at the end of extraction reports new types/parties. Use `sync --pattern` to fix mistakes.

### Normalization
The single LLM call receives all known document types and issuing parties (scanned from processed JSON files). No fixed canonical list — processed files are the sole source of truth. Known values are matched exactly; new values get a slug-cased name suggested by the LLM.

### Three-Phase Pipeline (`pipeline` command)
1. **Phase 1 — Ingest**: Gather files from all sources (Gmail, mbox, archives) into raw folders
2. **Phase 2 — Classify**: Process each new file (dedup, classify, rename) + sync orphans
3. **Phase 3 — Organize**: Export to Excel, export by date, reconcile bank statements, merge PDFs

### Three-Tier Hashing
- **Fast hash** (`hash_file_fast`): SHA256 of raw bytes, 8 chars - for quick duplicate filtering (Stage 1)
- **Text hash** (`hash_file_text`): Extracts text from all PDF pages, aggressively normalizes (lowercase, ASCII-only, no whitespace), SHA256 8 chars - catches compression duplicates where bytes and pixels differ but text is identical (~10-50ms per file). Returns `None` for scanned/image-only PDFs (Stage 2)
- **Content hash** (`hash_file_content`): Renders all pages at 150 DPI, hashes pixel data - detects true duplicates even if PDF metadata differs (~1-2s per file). Fallback for PDFs without extractable text (Stage 3)

### Hash Caching (`HashCache`)
Content hashing is expensive (~1-2s per file). The `HashCache` class caches hash_file → hash_content and hash_file → hash_text mappings in `~/.config/papertrail/cache/hash_cache.yaml`:

1. Compute fast file hash (cheap, ~0.05s)
2. Check cache for existing mapping
3. If cache miss, compute content hash (expensive) and save to cache

```
hash_file "a1b2c3d4" → cache lookup → hit → return cached hash_content
hash_file "b2c3d4e5" → cache lookup → miss → compute hash_content → save → return
```

The `validate` task uses parallelization (`ProcessPoolExecutor`) for cache misses, providing ~4-8x speedup on cold cache and ~50-100x on warm cache.

### QR Code Extraction (`papertrail/qr/`)
QR code extraction for Portuguese invoice QR codes (Portaria 195/2020).

**Supported formats:**
- **Portuguese Invoice QR** (Portaria 195/2020): `A:NIF*B:NIF*D:FT*F:YYYYMMDD*O:amount*...`

**How it works:**
```
PDF → render pages at 300 DPI → pyzbar decode → detect QR type → parse → QRExtractedMetadata
```

**Key components:**
- `extract_metadata_from_qr(pdf_path)` - Single-QR entry point, returns `(QRExtractedMetadata, raw_data_dict)` tuple
- `extract_all_metadata_from_qr(pdf_path)` - Multi-QR entry point, returns `list[tuple[QRExtractedMetadata, dict]]` for ALL Portuguese invoice QR codes
- `is_portuguese_invoice_qr(content)` - Detection function
- `parse_portuguese_invoice_qr(qr_data)` - Parser for PT invoice QR codes

**Multi-QR (sub-documents):** When 2+ Portuguese invoice QR codes are detected in a single PDF (e.g., Shared Toll toll aggregator), each becomes a sub-document with independent metadata stored in `sub_documents`. The parent document gets LLM classification with multi-QR context injected into the system prompt (sub-document count + issuer NIFs), guiding the LLM to classify the aggregator/wrapper document rather than any embedded invoice or payment reference. QR data is not merged into the parent (`qrcode=null`). Each sub-document gets NIF-enriched `issuing_party` during extraction. In reconciliation, sub-documents are expanded as independent candidates for matching.

**Portuguese QR fields extracted:**
- `issue_date` from F field (YYYYMMDD → YYYY-MM-DD, stored as `date_issued` in sidecar JSON)
- `document_type` from D field (FT → invoice, FR → invoice-receipt, NC → invoice-credit, ND → invoice-debit, RC/RG → receipt, etc.)
- `total_amount` from O field (gross total)
- `issuer_tax_number` from A field (raw NIF without country prefix)
- `atcud` from H field (unique document code)
- `locale` from C field (country code → BCP-47 format, e.g., "pt-PT")

**Dependencies:** Requires `pyzbar` Python package and `zbar` system library:
```bash
# macOS
brew install zbar

# Linux
apt install libzbar0
```

### Bank Statement Classification (`papertrail/bank_statement/`)
Deterministic classification of XLSX bank statements (no LLM needed, confidence=1.0).

**Supported formats:**
- **Millennium BCP**: Portuguese bank export with header rows 1-6 (account, dates), column headers in row 8 ("Data Lancamento", "Descricao", "Montante")

**How it works:**
```
XLSX → openpyxl open → detect format (check row 8 headers) → parse metadata → DocumentMetadata
```

**Key components:**
- `classify_bank_statement(xlsx_path, file_hash)` - Main entry point, returns `DocumentMetadata` or `None`
- `detect_bank_format(xlsx_path)` - Returns `BankFormat` enum or `None`
- `is_bank_statement(xlsx_path)` - Quick check if file is a recognized format

**Classification output:**
- `document_type` = `"bank-statement"`, `issuing_party` = `"millennium-bcp"`
- `document_title` = account number (e.g., `"TEST-ACCOUNT-ALPHA"`)
- `date_issued` = `period_start` (first date of statement range)
- `source_extension` = `".xlsx"` (enables extension-aware file naming)
- `bank_statement` dict with format-specific data (account, period, transaction count)

**Hashing:** Uses `hash_file_fast` for both `hash_file` and `hash_content` (no pixel rendering for XLSX).

**Adding a new bank format:** Create a parser module in `papertrail/bank_statement/` with `can_parse(ws)` and `parse(xlsx_path)` functions, add it to `_PARSERS` registry in `extractor.py`.

### Image File Conversion (`papertrail/image_convert.py`)
Image files (PNG, JPG, JPEG, TIFF, TIF, BMP, WebP) in raw folders are automatically converted to PDF during extraction, then processed through the standard PDF classification pipeline.

**How it works:**
```
Image (PNG/JPG/...) → Pillow convert to RGB → save as PDF → temp dir → classify as PDF → copy to processed
```

**Key details:**
- Conversion happens in a `TemporaryDirectory` that is cleaned up after extraction completes
- Output filenames use `{stem}_{path_hash}.pdf` to avoid collisions from same-named images in different raw directories
- Multi-frame TIFF files are saved as multi-page PDFs (`save_all`/`append_images`)
- The converted PDF is the artifact that gets hashed, classified, and stored — no special handling downstream
- Supported extensions defined in `IMAGE_EXTENSIONS` constant in `papertrail/pdf.py`
- Log marker: `[IMG-CONVERT]`

### Reconciliation Output
Reconciliation writes a `.reconciliation.json` sidecar alongside each bank statement XLSX (non-destructive — original XLSX is never modified):
```
2026-01-01 - bank-statement - millennium-bcp - TEST-ACCOUNT-ALPHA - a1b2c3d4.xlsx
2026-01-01 - bank-statement - millennium-bcp - TEST-ACCOUNT-ALPHA - a1b2c3d4.reconciliation.json
```
The `.reconciliation.json` file contains: `source` (XLSX filename), `generated` (ISO timestamp), `summary` (total/reconciled/incomplete/unmatched/unmatched_files/reconciliation_rate), `matches` (array with row, date, description, amount, currency, transaction_category, method, confidence, reasoning, files, errors), `unmatched` (array with row, date, description, amount, currency, transaction_category), `unmatched_files` (array with file, date_issued, document_type, issuing_party, total_amount, currency).

**Three-tier output:** `reconciled` (matched + valid), `incomplete` (matched but failed validation), `unmatched` (no document match found).

### Reconciliation Validation Rules
Configurable in `profile.yaml` under `reconciliation.rules`. Rules are evaluated top-to-bottom, **first-match-wins**. Each rule classifies a transaction category and defines required document types with cardinality constraints.

**Rule fields:**
- `name` (str): Rule identifier, becomes `transaction_category` in output
- `match_description` (list[str], optional): Keywords matched case-insensitively (diacritics-stripped) against bank description
- `direction` (str, optional): `"credit"` (amount > 0) or `"debit"` (amount <= 0)
- `required_types` (dict): Document type patterns → cardinality. Key is `|`-separated type alternatives, value is `N` (exactly N) or `[min, max]` (`null` = unbounded)
- `companions` (list[str], optional): Rule names to group for sum-based matching. Transactions on the same date are summed and matched against a single document (e.g., bank fee + stamp duty matched against one combined bank note)
- `shared_types` (dict, optional): Type pattern → issuing party filter. Documents matching these patterns are linked across multiple transactions (e.g., Shared Toll receipts shared across matching transactions)
- `expected_page_count` (dict, optional): Type pattern → expected count. Flags documents with unexpected page counts as validation warnings

**Config-level fields:**
- `exclude_prefixes` (list[str], optional): Filename prefixes to exclude from candidate matching (candidates with these prefixes are skipped)

**Example:**
```yaml
reconciliation:
  exclude_prefixes: ["VND_"]
  rules:
    - name: bank-fee
      match_description: ["COMISSAO"]
      required_types:
        bank-note: 1
    - name: bank-fee-stamp-duty
      match_description: ["IMPOSTO SELO"]
      companions: [bank-fee]
      required_types: {}
    - name: default-debit
      direction: debit
      required_types:
        bank-note: 1
        "invoice|receipt": [1, null]
```

Transactions matching no rule are classified as `"unclassified"` (validation error). Sub-documents are transparent to validation (treated as regular documents). Default rules (if none configured) replicate the previous 3-category behavior (bank-fee, default-credit, default-debit).

In the pipeline, reconciliation runs in Phase 3 (Organize) — after all classification is complete.

### NIF Lookup (`papertrail/nif_lookup.py`)
Enriches issuer information using Portuguese tax numbers (NIFs) extracted from QR codes.

**How it works:**
```
QR Code → issuer_tax_number: "ISSUER-TAX-ID"
              ↓
         NIF Cache lookup (TIER 1)
              ↓
         [hit] → use cached issuer name
         [miss] → scrape https://www.nif.pt/{NIF}/ (TIER 2) → cache → use
              ↓
         Override issuing_party with official name
              ↓
         Re-normalize to canonical form
```

Note: Tax numbers are stored WITHOUT country prefix (e.g., "ISSUER-TAX-ID" not "PTISSUER-TAX-ID"). The locale field captures the country context instead.

**Configuration in profile:**
```yaml
nif_api:
  enabled: true  # No API key required - uses public nif.pt URLs
```

**Cache file:** `~/.config/papertrail/cache/nif_cache.yaml` - stores NIF → issuer mappings to avoid repeated web lookups

**Logging markers:** `[NIF-CACHE-HIT]`, `[NIF-WEB-LOOKUP]`, `[NIF-NOT-FOUND]`, `[NIF-ENRICH]`

### Dynamic Enums
Document types and issuing parties are loaded dynamically from existing metadata JSON files in the processed directory. No fixed canonical list — processed files are the sole source of truth. Always includes `$UNKNOWN$` sentinel.

## Key Files

| File | Purpose | Key Lines |
|------|---------|-----------|
| `main.py` | Core app | Entry point, CLI tasks |
| `papertrail/models.py` | Pydantic models | `DocumentMetadataRaw`, `DocumentMetadata`, `SubDocumentMetadata` |
| `papertrail/llm.py` | LLM classification | `get_system_prompt_classify`, `normalize_issuing_party` |
| `papertrail/logging_utils.py` | Logging infrastructure | `setup_task_logging`, `DocumentLogger`, `setup_logging` |
| `papertrail/hashing.py` | File hashing | `HashCache`, `hash_file_fast`, `hash_file_text`, `hash_file_content` |
| `papertrail/nif_lookup.py` | NIF → issuer lookup | `NIFLookupCache` class |
| `scripts/check_hash.py` | Verify hashes | CLI: `check-hash` |
| `scripts/deduplicate.py` | Text-hash deduplication | `plan` + `execute` two-phase workflow |
| `papertrail/gmail.py` | Gmail API client | `GmailDownloader`, `download_gmail_attachments` |
| `papertrail/mbox.py` | Mbox extraction | `extract_mbox_attachments` |
| `papertrail/qr/` | QR code extraction | `extract_metadata_from_qr`, `extract_all_metadata_from_qr`, `parse_portuguese_invoice_qr` |
| `papertrail/bank_statement/` | XLSX bank statement classification | `classify_bank_statement`, `detect_bank_format` |
| `papertrail/image_convert.py` | Image-to-PDF conversion | `convert_image_to_pdf`, `convert_images_to_pdfs` |
| `papertrail/tasks/reconciliation.py` | Bank reconciliation | `task_reconcile`, `_discover_bank_statements` |
| `papertrail/tasks/archive.py` | Document archival | `task_archive` |
| `tools/browse.py` | Gradio document browser | Search, filter, preview processed docs |
| `tools/dedupe.py` | Gradio duplicate review UI | Visual dedup plan review + execute |
| `tools/review.py` | Gradio reconciliation review UI | Transaction table + document preview |

## Data Models (Pydantic)

```python
DocumentMetadataRaw    # Single-call: raw text + normalized fields from LLM
DocumentMetadata       # Full: hashes, timestamps, raw values
```

Fields: `bank_statement`, `class_confidence`, `class_reasoning`, `date_created`, `date_issued`, `date_updated`, `document_type`, `document_type_raw`, `document_title`, `file_size_kb`, `hash_content`, `hash_file`, `hash_text`, `issuer_tax_number`, `issuing_party`, `issuing_party_raw`, `locale`, `page_count`, `qrcode`, `source_extension`, `sub_documents`, `total_amount`, `total_amount_currency`

The `document_title` field stores the specific subject, product, service, or transaction described in the document (e.g., "YouTube Premium", "Claude API"). It is null when no specific subject beyond the document type is identifiable. The `document_type` / `document_type_raw` fields contain only the cleaned core type label (e.g., "Fatura").

The `qrcode` field stores raw QR code data when extracted:
```json
{
    "qrcode": {
        "qr_type": "portuguese_invoice",
        "raw_content": "A:ISSUER-TAX-ID*B:TESTCOMPANY*C:PT*D:FT*...",
        "page_number": 0
    }
}
```
Documents without QR codes have `"qrcode": null`.

The `bank_statement` field stores format-specific data for bank statements:
```json
{
    "bank_statement": {
        "bank_format": "millennium_bcp",
        "account_number": "TEST-ACCOUNT-ALPHA",
        "currency": "EUR",
        "period_start": "2026-01-01",
        "period_end": "2026-01-31",
        "transaction_count": 42
    }
}
```
Non-bank-statement documents have `"bank_statement": null`.

The `source_extension` field stores the original file extension when it's not `.pdf` (e.g., `".xlsx"` for bank statements). When `null`, defaults to `.pdf`. Used by `file_name_from_metadata()` and `find_companion_file()` to resolve the correct document file.

The `hash_text` field stores the text-based hash of a PDF document (first 8 hex chars of SHA256 of normalized text). `null` for scanned/image-only PDFs and XLSX files. Used as an intermediate dedup tier between `hash_file` (byte-level) and `hash_content` (pixel-level) to catch compression duplicates cheaply.

The `file_size_kb` field stores the companion document file size in kilobytes (rounded integer). Set during extraction for both PDFs and XLSX files.

The `sub_documents` field stores metadata for individual invoices within a multi-invoice PDF (e.g., Shared Toll toll aggregator PDFs with multiple QR codes from different issuers). When 2+ Portuguese invoice QR codes are detected, each is stored as a sub-document with independently NIF-enriched metadata. The parent document gets LLM-classified metadata (aggregator info) while `qrcode` is set to `null`. Each sub-document participates individually in reconciliation matching.
```json
{
    "sub_documents": [
        {
            "date_issued": "2025-01-15",
            "document_type": "receipt",
            "total_amount": 18.00,
            "total_amount_currency": "EUR",
            "issuer_tax_number": "SUBDOC-TAX-ID",
            "issuing_party": "brisa",
            "issuing_party_raw": "Brisa - Concessão Rodoviária, S.A.",
            "document_number": "DOC-NUMBER",
            "atcud": "...",
            "locale": "pt-PT",
            "qrcode": {"qr_type": "portuguese_invoice", "raw_content": "A:SUBDOC-TAX-ID*...", "page_number": 0}
        }
    ]
}
```
Documents with 0-1 QR codes have `"sub_documents": null`. `SubDocumentMetadata` model used for construction, `.model_dump()` for storage.

## File Naming Convention

Pattern: `YYYY-MM-DD - document-type - issuing-party - [title] - [amount currency] - hash_file.{ext}`
Example (PDF): `2025-01-02 - invoice - anthropic - claude api - 120 eur - a1b2c3d4.pdf`
Example (XLSX): `2026-01-01 - bank-statement - millennium-bcp - TEST-ACCOUNT-ALPHA - a1b2c3d4.xlsx`

The hash component is `hash_file` (SHA256 of raw bytes, 8 chars). This ensures every distinct file gets a unique filename, even if two files have identical visual content (which would share the same `hash_content`). Generated by `file_name_from_metadata()`. All components lowercase, sanitized.

## CLI Commands

| Command | Purpose | Options |
|---------|---------|---------|
| `pipeline` | Full end-to-end workflow (default) | `--months`, `--export_date` |
| `extract` | Process new PDFs/XLSX from raw folder | `processed_path?`, `--raw_path` |
| `sync` | Sync metadata (default: orphans only) | `processed_path?`, `--pattern`, `--dry_run`, `--all_unknown`, `-w`, `--all` |
| `check` | Verify integrity, fill missing fields, audit report | `processed_path?`, `--verify-hashes`, `--dry_run` |
| `reconcile` | Reconcile bank transactions against documents | `--export_path`, `--excel_path`, `--dry_run` |
| `gmail` | Download email attachments from Gmail | `--months` |
| `rename` | Rename files based on metadata | `processed_path?` |
| `archive` | Archive documents by hash digest | `digest...` (required), `processed_path?`, `--dry_run` |
| `export excel` | Export metadata to Excel | `processed_path?`, `--output` (required) |
| `export dates` | Export files by date range | `processed_path?`, `--base_dir`, `--run_merge` |
| `export copy` | Copy files matching pattern | `processed_path?`, `--pattern` (req), `--dest` (req) |

`processed_path?` = optional positional, defaults from profile. Global options: `--profile`, `-v`/`--verbose`.

## Configuration

### Profile-Based (Recommended)

Each profile is a self-contained folder under `~/.config/papertrail/profiles/`. Profiles are loaded as `Config` objects — a thin dict wrapper with dot-path access (`profile.openrouter.model_id`). No typed dataclasses; the YAML structure is the schema. Current setup: `~/.config/papertrail/profiles/default/profile.yaml`

```yaml
profile:
  name: "default"
  description: "Default configuration"
  tax_number: "TESTOWNER"  # Optional: your tax number (NIF)

paths:
  raw: ["/path/to/raw/documents/"]
  processed: "/path/to/processed/documents/"
  export: "/path/to/export/documents/"

openrouter:
  model_id: "google/gemini-2.5-flash"
  api_key: "YOUR_KEY_HERE"
```

**Usage**:
```bash
python main.py --profile default extract /path/to/processed
python main.py --profile personal pipeline
python main.py extract /path/to/processed  # Auto-uses default profile if available
```

**Multiple environments**: Create `~/.config/papertrail/profiles/personal/profile.yaml`, `~/.config/papertrail/profiles/work/profile.yaml`, etc. from templates in `profiles/profile.yaml.example`

**Full docs**: See `profiles/README.md` for complete YAML schema and examples

### Export Prefix Rules with Profile Variables

Export match rules support `${profile.*}` variable syntax to reference profile-level configuration. This enables distinguishing vendor invoices (VND — you issued them) from company invoices (CMP — you received them) by comparing `issuer_tax_number` against the profile owner's tax number. Rules are **first-match-wins**. Match patterns support trailing wildcards (`bank-*`), numeric comparison operators (`>1`, `>=10`, `<5`, `<=100`, `!=0`), `${profile.*}` variable substitution, nested fields (`qrcode.qr_type`), and the derived boolean `has_qrcode` (true when the document or any sub-document has QR metadata).

```yaml
profile:
  tax_number: "TESTOWNER"

export:
  file_mappings:
    enabled: true
    default_prefix: "DIV_"
    rules:
      - match:
          document_type: "invoice"
          issuer_tax_number: "${profile.tax_number}"
        prefix: "VND_"      # My tax number = I issued it
      - match:
          document_type: "invoice"
          issuing_party: "utility-provider"
          page_count: ">1"
        prefix: "EXC_"      # Multi-page Utility Provider invoices
      - match:
          document_type: "invoice*"
          issuing_party: "shared-toll"
          has_qrcode: false
        prefix: "EXC_"      # Shared Toll invoice-like summaries without QR
      - match:
          document_type: "invoice"
        prefix: "CMP_"      # Someone else issued it
      - match:
          document_type: "bank-*"
        prefix: "BNC_"
```

### Logs Directory

Task runs create timestamped log files in `{processed_path}/logs/`:
- `logs/extract_new_YYYYMMDD_HHMMSS.log` — per-document extraction details with `[QR-EXTRACT]`, `[QR-MERGE]`, `[NIF-CACHE-HIT]`, `[NIF-WEB-LOOKUP]`, `[NIF-ENRICH]`, `[CLASSIFY]`, `[TIMING]`, `[FINAL]` markers
- `logs/sync_YYYYMMDD_HHMMSS.log` — sync with before/after diffs
- `logs/pipeline_YYYYMMDD_HHMMSS.log` — full pipeline run
- `logs/classification_failures.log` — failure tracebacks (appended)

### Profile Data Files

**Profile-specific files** in `~/.config/papertrail/profiles/<name>/`:
- `profile.yaml` - Profile configuration (copy from `~/.config/papertrail/profiles/profile.yaml.example` or repo `profiles/profile.yaml.example`)

**Cache files** in `~/.config/papertrail/cache/` (auto-generated):
- `hash_cache.yaml` - File hash → content hash cache for fast validation
- `nif_cache.yaml` - NIF → issuer name cache for fast lookups
- `.extract.lock` - Extraction lock file (runtime state)

**Credentials** in `~/.config/papertrail/credentials/`:
- `gmail_credentials.json` - Gmail OAuth2 client credentials
- `gmail_token.json` - Gmail OAuth2 refresh token

## Code Patterns

- **Progress bars**: Always use `tqdm` for loops over files
- **Error handling**: Log failures to `classification_failures.log` via `failure_logger`
- **Validators**: Pydantic `@field_validator` for normalization (currency symbols, amounts, dates)
- **LLM calls**: Use `tool_choice` for structured output, temperature=0 for determinism
- **Fallbacks**: Always fall back to `$UNKNOWN$` for unrecognized values
- **Task logging**: Use `task_log_context(processed_path, task_name)` context manager from `papertrail/tasks/__init__.py` for unified log setup

## Common Development Tasks

**Add new document type**: Just process documents with that type - it's automatically added from metadata
**Add new issuing party**: Same - dynamically loaded from processed metadata
**Verify duplicate detection**: `check-hash <pdf>` shows both fast and content hashes
**Add new QR format**: Add detection function and parser in `papertrail/qr/extractor.py`, add model in `papertrail/qr/models.py`
**Add new bank format**: Create parser module in `papertrail/bank_statement/`, implement `can_parse(ws)` and `parse(xlsx_path)`, add to `_PARSERS` in `extractor.py`
**Deduplicate files**: `python scripts/deduplicate.py plan <dir>` to generate plan, review `_dupes_plan.json`, then `python scripts/deduplicate.py execute <dir>` to move dupes to `_dupes/` subfolder. Use `--dry-run` on execute to preview.

## Gradio Dev Tools (`tools/`)

Standalone Gradio apps for interactive document management. Run with `python tools/<app>.py`.

- **`browse.py`**: Document browser — search, navigate (j/k keys), preview PDFs/XLSX, view metadata as JSON
- **`dedupe.py`**: Duplicate review — scan for duplicates (text/content hash), approve/reject (a/r keys), execute moves to `_dupes_YYYYMMDD_HHMMSS/`
- **`review.py`**: Reconciliation audit — transaction tables color-coded by match status (exact/LLM/incomplete/unmatched), clickable file previews, unmatched files summary

## Testing

No test suite currently.

## Dependencies

Core: `openai`, `PyMuPDF (fitz)`, `pandas`, `pydantic`, `pyyaml`, `pillow`, `tqdm`, `openpyxl`, `mbox-extractor`
QR extraction: `pyzbar` (requires system `zbar` library)
Gmail: `google-api-python-client`, `google-auth-httplib2`, `google-auth-oauthlib`
Build: `hatchling`, Package manager: `uv`
