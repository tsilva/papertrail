# Pipeline

The `pipeline` task runs the full end-to-end document processing workflow. It requires three external tools (`mbox-extractor`, `archive-extractor`, `pdf-merger`) and profile configuration with `paths.raw`, `paths.processed`, and `paths.export` set.

```
python main.py [--profile NAME] pipeline [--export_date YYYY-MM]
```

If `--export_date` is not provided, it defaults to last month.

## Steps

### Step 1: Download Gmail attachments
**Task:** `gmail_download` | **File:** `papertrail/gmail.py`

Downloads PDF attachments from Gmail using the Google API. The date range starts from the most recent month found in processed files (or last 30 days if none exist) through today. Downloads to the first path in `paths.raw`.

### Step 2: Extract emails from mbox files
**Tool:** `mbox-extractor`

Runs against each raw directory. Extracts email attachments from Google Takeout `.mbox` files found in the raw paths.

### Step 3: Extract archives
**Tool:** `archive-extractor`

Runs against each raw directory. Extracts files from ZIP/archive files, using passwords from profile configuration (`passwords` setting) if available.

### Step 4: Extract new documents
**Task:** `extract_new` | **File:** `main.py:619`

Processes new PDFs through the classification pipeline:

1. **Build hash index** from existing metadata in the processed directory
2. **Fast hash filter** (SHA256 of raw bytes, 8 chars) — skips files already in the index
3. **Content hash** for remaining files (renders all pages at 150 DPI, hashes pixel data) — catches true duplicates even with different PDF metadata
4. **Classify** each truly new PDF:
   - **Phase 1 — Raw extraction** (`classify_pdf_document`, line 175): Renders first 2 pages as JPEG, sends to LLM via OpenRouter with vision, extracts metadata exactly as it appears on the document into `DocumentMetadataRaw`
   - **Phase 2 — Normalization** (`normalize_metadata` in `papertrail/llm.py`): Maps raw values to canonical enums using two-tier lookup:
     - **Tier 1 — Mappings lookup**: Checks `config/mappings.yaml` for known raw-to-canonical mappings (no LLM call)
     - **Tier 2 — LLM fallback**: If not found, uses LLM to normalize, then saves the new mapping for reuse
5. **Save** the classified PDF (copied to processed directory) and its metadata JSON sidecar file

### Step 5: Rename files
**Task:** `rename_files` | **File:** `main.py:663`

Renames PDFs and their metadata JSON files based on current metadata using the naming convention:

```
YYYY-MM-DD - document-type - issuing-party - [service] - [amount currency] - hash.pdf
```

This ensures filenames reflect any metadata corrections made since initial processing.

### Step 6: Export metadata to Excel
**Task:** `export_excel` | **File:** `main.py:418`

Exports all metadata to `processed_files.xlsx` in the processed directory. Includes fields like issue date, document type, issuing party, service name, amounts, hashes, and page counts. Sorted by issue date descending.

### Step 7: Copy matching documents
**Task:** `copy_matching` | **File:** `main.py:488`

Copies PDFs and metadata JSONs matching the export date pattern (e.g., `2025-01`) from the processed directory to `export/<YYYY-MM>/`. Uses incremental mode — skips files that already exist with identical content.

### Step 8: Merge PDFs
**Tool:** `pdf-merger`

Merges all PDFs in the export date directory into `merged_all.pdf`. After merging, validates that the merged PDF page count equals the sum of all source PDF page counts.

### Step 9: Validate exported files
**Task:** `check_files_exist` | **File:** `main.py:576`

Validates that expected files exist in the export directory by checking against validation rules from the profile configuration. Skipped if no validation rules are configured.

## Data Flow

```
Gmail / mbox / archives
        │
        ▼
   Raw PDFs in paths.raw
        │
        ▼
  Fast hash ──► known? skip
        │ no
        ▼
  Content hash ──► known? skip
        │ no
        ▼
  LLM Vision (Phase 1: raw extraction)
        │
        ▼
  Normalization (Phase 2: mappings → LLM fallback)
        │
        ▼
  PDF + metadata.json in paths.processed
        │
        ▼
  Rename ──► Excel export ──► Copy to export/YYYY-MM/ ──► Merge ──► Validate
```
