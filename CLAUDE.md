# papertrail - Claude Code Context

AI-powered PDF document classification and organization tool using vision LLMs via OpenRouter.

## Quick Reference

**Run**: `python main.py [--profile NAME] <task> <processed_path> [options]`
**Install**: `uv pip install -e .`
**Check hashes**: `python scripts/check_hash.py <pdf_path>`
**Profile docs**: `profiles/README.md` - Multi-environment configuration system

## Architecture

### Four-Phase Extraction Pipeline
1. **Phase 0 - QR Extraction** (optional): Scans PDF for QR codes, extracts metadata with 100% confidence (e.g., Portuguese invoice QR codes)
2. **Phase 1 - Raw Extraction** (`classify_pdf_document`): Renders first 2 pages as JPEG, sends to LLM with vision, extracts metadata EXACTLY as appears on document
3. **Phase 2 - Normalization** (`normalize_metadata`): Maps raw values to canonical enums using two-tier lookup
4. **Phase 3 - Merge**: QR-extracted values override LLM values (QR is 100% accurate)
5. **Phase 4 - NIF Enrichment** (optional): If tax number present, looks up official issuer name via nif.pt web scraping

### Two-Tier Normalization (Mapping Persistence)
Ensures deterministic normalization by persisting successful mappings:

1. **TIER 1 - Mappings Lookup** (`MappingsManager`): Check `config/mappings.yaml` for known raw → canonical mappings (instant, no LLM call)
2. **TIER 2 - LLM Fallback**: If not found, use LLM to normalize, then save mapping for reuse

```
Raw: "Anthropic, PBC" → Check mappings.yaml → Found! → Return "anthropic" (no LLM)
Raw: "New Vendor Inc" → Check mappings.yaml → Not found → LLM → "new-vendor" → Save to mappings.yaml
```

Mappings file structure:
- `confirmed`: User-validated mappings (trusted)
- `auto`: LLM-generated mappings (pending review via `review_mappings` task)
- `canonicals`: Master list of valid canonical values

### Two-Tier Hashing
- **Fast hash** (`hash_file_fast`): SHA256 of raw bytes, 8 chars - for quick duplicate filtering
- **Content hash** (`hash_file_content`): Renders all pages at 150 DPI, hashes pixel data - detects true duplicates even if PDF metadata differs

### Hash Caching (`HashCache`)
Content hashing is expensive (~1-2s per file). The `HashCache` class caches file_hash → content_hash mappings in `config/hash_cache.yaml`:

1. Compute fast file hash (cheap, ~0.05s)
2. Check cache for existing mapping
3. If cache miss, compute content hash (expensive) and save to cache

```
file_hash "a1b2c3d4" → cache lookup → hit → return cached content_hash
file_hash "b2c3d4e5" → cache lookup → miss → compute content_hash → save → return
```

The `validate_metadata` task uses parallelization (`ProcessPoolExecutor`) for cache misses, providing ~4-8x speedup on cold cache and ~50-100x on warm cache.

### QR Code Extraction (`papertrail/qr/`)
Modular QR code extraction with handler-based architecture for different QR formats.

**Supported formats:**
- **Portuguese Invoice QR** (Portaria 195/2020): `A:NIF*B:NIF*D:FT*F:YYYYMMDD*O:amount*...`

**How it works:**
```
PDF → render pages at 300 DPI → pyzbar decode → detect QR type → parse with handler → QRExtractedMetadata
```

**Key components:**
- `extract_metadata_from_qr(pdf_path)` - Main entry point
- `QRHandler` - Abstract base class for format handlers
- `PortugueseInvoiceHandler` - Parses PT invoice QR codes

**Portuguese QR fields extracted:**
- `issue_date` from F field (YYYYMMDD → YYYY-MM-DD)
- `document_type` from D field (FT → invoice, NC → credit-note, etc.)
- `total_amount` from O field (gross total)
- `issuer_nif` from A field (tax ID)
- `atcud` from H field (unique document code)

**Dependencies:** Requires `pyzbar` Python package and `zbar` system library:
```bash
# macOS
brew install zbar

# Linux
apt install libzbar0
```

### NIF Lookup (`papertrail/nif_lookup.py`)
Enriches issuer information using Portuguese tax numbers (NIFs) extracted from QR codes.

**How it works:**
```
QR Code → issuer_tax_number: "PTISSUER-TAX-ID"
              ↓
         Strip "PT" prefix → "ISSUER-TAX-ID"
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

**Configuration in profile:**
```yaml
nif_api:
  enabled: true  # No API key required - uses public nif.pt URLs
```

**Cache file:** `config/nif_cache.yaml` - stores NIF → issuer mappings to avoid repeated web lookups

**Logging markers:** `[NIF-CACHE-HIT]`, `[NIF-WEB-LOOKUP]`, `[NIF-NOT-FOUND]`, `[NIF-ENRICH]`

### Dynamic Enums
Document types and issuing parties are loaded dynamically from existing metadata JSON files in the processed directory. Falls back to hardcoded lists if directory doesn't exist. Always includes `$UNKNOWN$` sentinel.

## Key Files

| File | Purpose | Key Lines |
|------|---------|-----------|
| `main.py` | Core app | Entry point, CLI tasks |
| `papertrail/models.py` | Pydantic models | `DocumentMetadataRaw`, `DocumentMetadata` |
| `papertrail/llm.py` | LLM classification | `normalize_metadata` with two-tier lookup |
| `papertrail/mappings.py` | Mapping persistence | `MappingsManager` class |
| `papertrail/rejected.py` | Rejected values tracking | `RejectedValuesManager` class |
| `papertrail/logging_utils.py` | Logging infrastructure | `setup_task_logging`, `DocumentLogger`, `setup_logging` |
| `papertrail/hashing.py` | File hashing | `HashCache`, `hash_file_fast`, `hash_file_content` |
| `papertrail/nif_lookup.py` | NIF → issuer lookup | `NIFLookupCache` class |
| `scripts/check_hash.py` | Verify hashes | CLI: `check-hash` |
| `papertrail/gmail.py` | Gmail API client | `GmailDownloader`, `download_gmail_attachments` |
| `papertrail/mbox.py` | Mbox extraction | `extract_mbox_attachments` |
| `papertrail/qr/` | QR code extraction | `extract_metadata_from_qr`, `PortugueseInvoiceHandler` |
| `papertrail/tasks/qr_inventory.py` | QR inventory task | `task_qr_inventory` |

## Data Models (Pydantic)

```python
DocumentMetadataRaw    # Phase 1: exact text from document
DocumentMetadataInput  # With enum validation
DocumentMetadata       # Full: hashes, timestamps, raw values
```

Fields: `issue_date`, `document_type`, `issuing_party`, `service_name`, `total_amount`, `total_amount_currency`, `confidence`, `reasoning`, `content_hash`, `file_hash`, `create_date`, `update_date`, `document_type_raw`, `issuing_party_raw`, `page_count`

## File Naming Convention

Pattern: `YYYY-MM-DD - document-type - issuing-party - [service] - [amount currency] - hash.pdf`
Example: `2025-01-02 - invoice - anthropic - claude-api - 120 eur - a1b2c3d4.pdf`

Generated by `file_name_from_metadata()` (line 447). All components lowercase, sanitized.

## CLI Tasks

| Task | Purpose | Required Options |
|------|---------|------------------|
| `extract_new` | Process new PDFs from raw folder | `--raw_path` |
| `rename_files` | Rename based on metadata | - |
| `validate_metadata` | Check consistency | - |
| `export_excel` | Export to Excel | `--excel_output_path` |
| `copy_matching` | Copy files matching regex | `--regex_pattern`, `--copy_dest_folder` |
| `export_all_dates` | Export by date range | `--export_base_dir` |
| `check_files_exist` | Validate against schema | `--check_schema_path` (optional) |
| `pipeline` | Full end-to-end workflow | `--export_date` (optional) |
| `gmail_download` | Download email attachments from Gmail | None (uses profile) |
| `bootstrap_mappings` | Populate mappings from existing metadata | - |
| `review_mappings` | Interactive review of auto-added mappings | - |
| `add_canonical` | Add a new canonical value | `--field`, `--canonical` |
| `backfill_page_count` | Add page_count to existing metadata | - |
| `review_rejected` | Review rejected normalization values | - |
| `reextract` | Re-extract documents (requires targeting flag) | `--all_unknown`, `--filename`, `--document_pattern`, `--dry_run` |
| `validate_extraction` | Validate extraction quality, flag issues | `--document_pattern` (optional) |
| `qr_inventory` | Scan PDFs for QR codes, create inventory | `--export_path` (optional, uses profile) |

## Configuration

### Profile-Based (Recommended)

**Current setup**: `profiles/default.yaml` (migrated from `.env`)

```yaml
profile:
  name: "default"
  description: "Default configuration"

paths:
  raw: ["/path/to/raw/documents/"]
  processed: "/path/to/processed/documents/"
  export: "/path/to/export/documents/"

openrouter:
  model_id: "google/gemini-2.5-flash"
  api_key: "YOUR_KEY_HERE"

document_types:
  predefined: null  # Dynamic loading from processed metadata
```

**Usage**:
```bash
python main.py --profile default extract_new /path/to/processed
python main.py --profile personal pipeline
python main.py extract_new /path/to/processed  # Auto-uses default.yaml if available
```

**Multiple environments**: Create `profiles/personal.yaml`, `profiles/work.yaml`, etc. from templates in `profiles/*.yaml.example`

**Full docs**: See `profiles/README.md` for complete YAML schema and examples

### Logs Directory

Task runs create timestamped log files in `{processed_path}/logs/`:
- `logs/extract_new_YYYYMMDD_HHMMSS.log` — per-document extraction details with `[QR-EXTRACT]`, `[QR-MERGE]`, `[NIF-CACHE-HIT]`, `[NIF-WEB-LOOKUP]`, `[NIF-ENRICH]`, `[RAW]`, `[TIER-1-HIT]`, `[TIER-2-LLM]`, `[TIMING]`, `[FINAL]` markers
- `logs/reextract_YYYYMMDD_HHMMSS.log` — re-extraction with before/after diffs
- `logs/pipeline_YYYYMMDD_HHMMSS.log` — full pipeline run
- `logs/validate_extraction_YYYYMMDD_HHMMSS.log` — extraction quality audit
- `logs/qr_inventory_YYYYMMDD_HHMMSS.log` — QR code inventory scan
- `logs/classification_failures.log` — failure tracebacks (appended)

### Config Files

**Config files** in `config/` directory (gitignored, copy from `.example` files):
- `mappings.yaml` - Raw → canonical mappings for deterministic normalization (see `mappings.yaml.example`)
- `rejected_values.yaml` - Rejected normalizations for review (auto-generated, see `review_rejected` task)
- `hash_cache.yaml` - File hash → content hash cache for fast validation (auto-generated)
- `nif_cache.yaml` - NIF → issuer name cache for fast lookups (see `nif_cache.yaml.example`)
- `qr_inventory.yaml` - QR code inventory results (auto-generated by `qr_inventory` task)
- `passwords.txt` - ZIP extraction passwords
- `file_check_validations.json` - File validation schema
- `document_types.json` - Fallback document types for classification
- `gmail_credentials.json` - Gmail OAuth2 client credentials (from Google Cloud Console)
- `gmail_settings.json` - Gmail download settings (legacy, use profile settings instead)
- `gmail_token.json` - Auto-generated OAuth2 refresh token

## Code Patterns

- **Progress bars**: Always use `tqdm` for loops over files
- **Error handling**: Log failures to `classification_failures.log` via `failure_logger`
- **Validators**: Pydantic `@field_validator` for normalization (currency symbols, amounts, dates)
- **LLM calls**: Use `tool_choice` for structured output, temperature=0 for determinism
- **Fallbacks**: Always fall back to `$UNKNOWN$` for unrecognized values

## Common Development Tasks

**Add new document type**: Just process documents with that type - it's automatically added from metadata
**Add new issuing party**: Same - dynamically loaded from processed metadata
**Verify duplicate detection**: `check-hash <pdf>` shows both fast and content hashes
**Add new QR handler**: Create handler in `papertrail/qr/handlers/`, implement `QRHandler` interface, register in `handlers/__init__.py`

### Mappings Workflow

1. **Bootstrap from existing data**: `python main.py bootstrap_mappings /path/to/processed`
   - Scans existing metadata files, extracts raw → canonical pairs
   - Adds them as 'confirmed' mappings

2. **Process new documents**: `python main.py extract_new /path/to/processed --raw_path /path/to/raw`
   - Known mappings use Tier 1 (no LLM call)
   - New values go through Tier 2 (LLM), then saved as 'auto'
   - Values rejected by validation are logged to `config/rejected_values.yaml`

3. **Review auto mappings**: `python main.py review_mappings`
   - Interactive review: confirm, edit, or reject each mapping

4. **Review rejected values**: `python main.py review_rejected`
   - When LLM suggests a canonical not in the allowed list, it's logged as rejected
   - Review to: add as new canonical, map to existing canonical, or ignore

5. **Add new canonical**: `python main.py add_canonical --field issuing_party --canonical "new-vendor"`
   - Makes a new canonical value available for future mappings

6. **Fix existing $UNKNOWN$ values**: `python main.py fix_unknown /path/to/processed`
   - Re-normalizes documents that have $UNKNOWN$ using stored raw values
   - Use `--dry_run` to preview changes without modifying files
   - After fixing, run `rename_files` to update filenames

## Testing

No test suite currently.

## Dependencies

Core: `openai`, `PyMuPDF (fitz)`, `pandas`, `pydantic`, `pyyaml`, `pillow`, `tqdm`, `openpyxl`, `mbox-extractor`
QR extraction: `pyzbar` (requires system `zbar` library)
Gmail: `google-api-python-client`, `google-auth-httplib2`, `google-auth-oauthlib`
Build: `hatchling`, Package manager: `uv`
