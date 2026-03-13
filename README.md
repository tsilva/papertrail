# papertrail

AI-powered document classification and organization for PDFs, images, and bank-statement XLSX files.

papertrail preserves a simple public contract:

- Typer CLI commands in [`main.py`](/Users/tsilva/repos/tsilva/papertrail/main.py)
- profile YAML under `~/.config/papertrail/profiles/<name>/profile.yaml`
- sidecar JSON as the source of truth
- deterministic file naming from metadata
- Gradio tools in [`tools/browse.py`](/Users/tsilva/repos/tsilva/papertrail/tools/browse.py), [`tools/dedupe.py`](/Users/tsilva/repos/tsilva/papertrail/tools/dedupe.py), and [`tools/review.py`](/Users/tsilva/repos/tsilva/papertrail/tools/review.py)

## Install

papertrail requires Python 3.12+.

```bash
git clone https://github.com/tsilva/papertrail.git
cd papertrail
uv pip install -e .
```

For local development and tests:

```bash
uv pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

## Configure

papertrail uses profile-based configuration. Create a profile at:

```text
~/.config/papertrail/profiles/default/profile.yaml
```

Example:

```yaml
profile:
  name: "default"
  description: "Default configuration"
  tax_number: "TESTOWNER"

paths:
  raw:
    - "/Users/you/Documents/papertrail/raw"
  processed: "/Users/you/Documents/papertrail/processed"
  export: "/Users/you/Documents/papertrail/export"

openrouter:
  model_id: "google/gemini-2.5-flash"
  api_key: "YOUR_OPENROUTER_KEY"

gmail:
  enabled: false

nif_api:
  enabled: true
```

Full profile documentation lives in [`profiles/README.md`](/Users/tsilva/repos/tsilva/papertrail/profiles/README.md).

## Run

Use either the entrypoint or `python3 main.py`:

```bash
papertrail --profile default pipeline
python3 main.py --profile default pipeline
```

Core commands:

| Command | Purpose |
|---|---|
| `pipeline` | Full ingest -> classify -> organize workflow |
| `extract` | Process new PDFs, images, and XLSX files from raw folders |
| `sync` | Rebuild or repair metadata from sidecars and companions |
| `rename` | Rename document files from sidecar JSON |
| `check` | Backfill missing metadata and audit integrity |
| `reconcile` | Reconcile bank statements against exported documents |
| `gmail` | Download Gmail attachments into raw folders |
| `archive` | Move documents by `hash_file` digest into `_archived/` |
| `export excel` | Export metadata to an Excel workbook |
| `export dates` | Export files into `YYYY-MM` folders |
| `export copy` | Copy files matching a pattern |

Examples:

```bash
python3 main.py extract
python3 main.py sync --all-unknown
python3 main.py check --verify-hashes
python3 main.py export excel --output /tmp/processed_files.xlsx
python3 main.py export copy --pattern 2026-01 --dest /tmp/january
python3 main.py reconcile
```

## Supported Inputs

- PDF documents classified with a vision LLM
- PNG, JPG, JPEG, TIFF, BMP, and WebP images converted to PDF on ingest
- XLSX bank statements classified deterministically
- Gmail attachments
- mbox attachments
- compressed archives discovered in raw folders

## Invariants

These are intentionally preserved:

1. Raw extracted values are stored in `*_raw` fields.
2. Deduplication uses `hash_content`; filenames use `hash_file`.
3. `$UNKNOWN$` is the only fallback sentinel.
4. QR metadata overrides LLM metadata.
5. Sidecar JSON is authoritative; `rename` fixes filenames from metadata.

## Outputs

Document filenames follow:

```text
YYYY-MM-DD - document-type - issuing-party - [title] - [amount currency] - hash_file.ext
```

Examples:

```text
2025-01-02 - invoice - anthropic - claude api - 120 eur - a1b2c3d4.pdf
2026-01-01 - bank-statement - millennium-bcp - TEST-ACCOUNT-ALPHA - a1b2c3d4.xlsx
```

Reconciliation writes a sidecar next to each bank statement:

```text
2026-01-01 - bank-statement - millennium-bcp - TEST-ACCOUNT-ALPHA - a1b2c3d4.reconciliation.json
```

## Gradio Tools

Run the local review tools directly:

```bash
python3 tools/browse.py
python3 tools/dedupe.py
python3 tools/review.py
```

## Notes

- QR extraction requires `pyzbar` and the system `zbar` library.
- Gmail requires credentials under `~/.config/papertrail/credentials/`.
- Logs are written under `{processed}/logs/`.
