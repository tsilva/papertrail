# Pipeline Console Output Spec

## General Principles

The pipeline console output follows a **flat step list** pattern: each stage is a single line that starts as a spinner and resolves to a status icon with a detail message. No stage numbers or phase groupings.

- Every step MUST report **concrete counts** (never just "Completed").
- Steps SHOULD highlight **notable items** when relevant (new issuers, `$UNKNOWN$` values, etc.).
- All warnings are shown **inline** (yellow `!`) AND **collected into a warnings recap** before the summary footer.
- Visual style: Rich-based, cyan rules, colored status icons (`green checkmark`, `yellow !`, `red X`).
- Long-running steps (extraction) use a **progress bar** while running, then show an aggregate summary line.
- Reconciliation shows **one line per bank statement** with individual match stats.

## Header

```
============================== PIPELINE ==============================
Profile: default | Log: logs/pipeline_20260211_143000.log
```

Unchanged from current implementation.

## Step-by-Step Output

### Ingest

```
✓ Download Gmail attachments — 5 messages, 3 new attachments
✓ Google Takeout mbox extraction — 2 mbox files, 8 attachments
✓ Google Takeout archive extraction — 14 files from 3 archives
```

When empty: `! No mbox files found`, `! No archives found`

### Extraction

```
Processing 15/15 files  ████████████████████████████████ 100%  0:00:32
✓ Extract new documents — 12 new, 3 duplicates (2 XLSX, 10 PDF)
```

Notable items (indented below, only when present):

```
✓ Extract new documents — 12 new, 3 duplicates
    New issuers: acme-corp, widgets-inc
    Unknown: 1 document_type, 2 issuing_party
```

### Sync

```
✓ Sync orphans — 0 orphans found
```

Or: `✓ Sync orphans — 3 orphans re-synced`

### Rename

```
✓ Rename files — 45 validated, 2 renamed
```

### Export to Excel

```
✓ Export to Excel — 45 entries
```

### Copy & Merge (per export month)

```
✓ Copy matching documents (2026-01) — 23 files
✓ Merge PDFs (2026-01) — 3 merged PDFs (CMP_, VND_, BNC_)
```

### Reconciliation (per bank statement)

```
✓ Reconcile: TEST-ACCOUNT-ALPHA (2026-01) — 34/40 matched (85%)
```

On failure: `! Reconcile: TEST-ACCOUNT-ALPHA (2026-01) — failed: no matching documents`

## Footer

```
⚠ Warnings:
  ! Gmail download failed, continuing pipeline (connection timeout)
  ! No mbox files found

Summary:
  Extracted:   15 new, 3 duplicates
  Renamed:     4 files
  Exported:    60 entries
  Copied:      40 files across 2 months
  Reconciled:  1 statement, 85% matched (34/40)

Output:
  Export: /path/to/export/2026-01/
  Export: /path/to/export/2026-02/
  Excel:  /path/to/processed/processed_files.xlsx
  Log:    logs/pipeline_20260211_143000.log

======================================================================
Pipeline completed in 52.3s
```

- **Warnings section** only appears if there were warnings during the run.
- **Summary** aggregates key counts from each stage. Zero values are omitted.
- **Output** shows paths to key artifacts.

## Edge Cases

### Fatal errors

```
✗ Extract new documents — OpenRouter API key not configured
```

Pipeline exits after the error line. No footer summary (incomplete run).

### Non-fatal warnings

```
! Gmail download failed, continuing pipeline (connection timeout)
```

Shown inline and collected for footer recap.

### Empty pipeline (nothing to process)

```
✓ Extract new documents — 0 new files
✓ Rename files — 12 validated, 0 renamed
✓ Export to Excel — 12 entries
✓ Copy matching documents (2026-01) — 0 files
```

Footer summary still shows with output paths; zero-count lines are omitted from summary.

### Skipped steps

- Gmail disabled in profile: step not shown (no "skipped" line).
- No bank statements found: reconciliation section doesn't appear.

### Multiple export months

Copy/merge/reconcile steps repeat per month, each labeled: `(2026-01)`, `(2026-02)`.

## Full Example

```
============================== PIPELINE ==============================
Profile: default | Log: logs/pipeline_20260211_143000.log

✓ Download Gmail attachments — 12 messages, 4 new attachments
✓ Google Takeout mbox extraction — 1 mbox file, 6 attachments
✓ Google Takeout archive extraction — 8 files from 2 archives
Processing 18/18 files  ████████████████████████████████ 100%  0:00:45
✓ Extract new documents — 15 new, 3 duplicates (1 XLSX, 14 PDF)
    New issuers: widgets-inc
    Unknown: 1 issuing_party
✓ Sync orphans — 0 orphans found
✓ Rename files — 60 validated, 4 renamed
✓ Export to Excel — 60 entries
✓ Copy matching documents (2026-01) — 28 files
✓ Merge PDFs (2026-01) — 3 merged PDFs (CMP_, VND_, BNC_)
✓ Reconcile: TEST-ACCOUNT-ALPHA (2026-01) — 34/40 matched (85%)
✓ Copy matching documents (2026-02) — 12 files
✓ Merge PDFs (2026-02) — 2 merged PDFs (CMP_, VND_)

⚠ Warnings:
  ! Unknown: 1 issuing_party in extracted documents

Summary:
  Extracted:   15 new, 3 duplicates
  Renamed:     4 files
  Exported:    60 entries
  Copied:      40 files across 2 months
  Reconciled:  1 statement, 85% matched (34/40)

Output:
  Export: /Users/tsilva/Google Drive/.../export/2026-01/
  Export: /Users/tsilva/Google Drive/.../export/2026-02/
  Excel:  /Users/tsilva/Google Drive/.../processed/processed_files.xlsx
  Log:    logs/pipeline_20260211_143000.log

======================================================================
Pipeline completed in 52.3s
```
