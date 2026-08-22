---
name: extract-last-month
description: Run Papertrail's recurring monthly document workflow for the previous calendar month using local AgentBridge with Codex, including raw-file ingestion, classification, filename repair, monthly export, bank-statement reconciliation, verification, anomaly triage, and a final status report. Use when asked to run, continue, verify, or repair the monthly extraction; ingest newly added Takeout documents for last month; ensure last month's reconciliation is complete; or report smells in a monthly Papertrail export.
---

# Extract Last Month

Run the repository-owned pipeline for the previous calendar month. Prove reconciliation from its sidecars, repair actionable failures, and report evidence-backed data smells.

## Establish the run

1. Work from the Papertrail repository root and read its `AGENTS.md` plus the live CLI surfaces before changing code.
2. Use the profile named by the user. Otherwise list `~/.config/papertrail/profiles/*/profile.yaml`; use the only profile when exactly one exists, and ask only when multiple profiles remain genuinely ambiguous.
3. Compute the previous calendar month in the user's local timezone as `YYYY-MM`. Do not infer it from the newest export folder.
4. Resolve `paths.raw`, `paths.processed`, `paths.export`, `openrouter.base_url`, and `openrouter.model_id` through `papertrail.config.load_profile`. Use every configured raw path, including Takeout; never move or delete raw originals.
5. Record the initial Git status and preserve unrelated changes. Create a temporary run directory and a timestamp marker immediately before running the pipeline so newly written processed sidecars can be distinguished during the audit.

## Require AgentBridge with Codex

Treat the historical `openrouter` profile key as configuration naming only. Require all of the following:

- The base URL points to loopback AgentBridge (`http://127.0.0.1:<port>/api/v1` or `http://localhost:<port>/api/v1`).
- The model ID starts with `codex/`.
- No OpenRouter or other remote-provider fallback is used.

Probe the configured URL. If it is unavailable, start `agentbridge` on the configured port, send its output to the temporary run directory, retain its exact PID, and poll briefly until the endpoint responds. If the endpoint was already running, leave it running. If this run started it, terminate only that recorded process after all extraction, repair, and verification work finishes.

Stop before extraction when the profile is not configured for AgentBridge + Codex. A profile change is user state: explain the mismatch and obtain approval before editing it.

## Run the target month

Use the locked repository environment and request only the target export month:

```bash
uv run --frozen papertrail --profile <profile> pipeline --months 2 --export-date <YYYY-MM>
```

The two-month input window accommodates late-arriving Gmail material when Gmail is enabled; `--export-date` still restricts regeneration and reconciliation to the target month. Capture the pipeline summary, warnings, classification failures, and emitted log path.

Do not treat exit code zero as proof of complete reconciliation. The pipeline records reconciliation failures as warnings in some paths.

## Audit deterministically

Run the bundled auditor after every pipeline or repair pass:

```bash
uv run --frozen python .agents/skills/extract-last-month/scripts/audit_month.py \
  --profile <profile> --month <YYYY-MM> --since-file <timestamp-marker>
```

The auditor exits nonzero unless the profile is AgentBridge/Codex-backed and every XLSX bank statement has a current, internally consistent reconciliation sidecar with at least one transaction, 100% reconciliation, zero incomplete matches, and zero unmatched transactions.

If durable groundtruth exists in the month folder, also run:

```bash
uv run --frozen papertrail --profile <profile> regression --export-date <YYYY-MM>
```

Never seed or update reconciliation approvals unless the user explicitly requests it.

## Repair until complete

Use source evidence rather than inferred filenames when repairing data.

1. For `$UNKNOWN$`, missing metadata, low confidence, or filename disagreement, inspect the source PDF/XLSX and its authoritative processed sidecar. Re-run `sync --all-unknown`, then `rename`, before making an evidence-backed manual correction.
2. For missing evidence, search all configured raw paths and processed documents by transaction date, amount, issuer, document number, and content/file hashes. Ingest newly found files and rerun the target-month pipeline.
3. For an incorrect association, inspect the bank row, every attached candidate, and the source documents. Correct metadata or profile reconciliation policy when the evidence warrants it, then regenerate the month.
4. For a reproducible Papertrail defect, add a focused regression test, make the smallest general fix, and follow every test and golden-regression requirement in `AGENTS.md`. Do not encode a one-document filename exception when a durable rule can express the behavior.
5. For an AgentBridge defect, collect its log and exact failure first. Modify AgentBridge only when the user has authorized changes outside Papertrail.
6. Re-run the auditor after each repair. Report reconciliation as complete only when its exit status is zero and every statement passes its checks.

If required evidence truly does not exist, stop the repair loop and report the exact unmatched rows and searched locations. Never invent a document or mark a transaction reconciled without supporting evidence.

## Triage data smells

Use the auditor output, pipeline warnings/logs, and direct source inspection. Check at least:

- newly created or target-month `$UNKNOWN$` values and unknown-bearing filenames;
- missing companions or critical fields, malformed/out-of-month dates, low classification confidence, and amount/currency inconsistencies;
- unexpected new issuers or document types, duplicate hashes, sudden volume/distribution changes versus adjacent months, and suspiciously absent categories;
- classification failures, orphan sidecars, filename/hash disagreements, and long filenames;
- LLM reconciliation matches, confidence below 1.0, matches with errors, and globally unmatched candidates that appear transaction-relevant.

Treat an observation as a smell only when the data provides concrete evidence. For example, a file unmatched by one bank statement but matched by another is not a smell. Inspect suspicious PDFs with the PDF workflow and suspicious XLSX data with the spreadsheet workflow rather than guessing from metadata alone.

Fix safe, evidence-backed metadata and reconciliation problems within the requested workflow. Preserve uncertain observations for the final report with filenames, hashes, rows, and amounts needed for follow-up.

## Report and clean up

Stop only the AgentBridge process started by this run, retain logs long enough to report failures, and leave pre-existing processes untouched.

Return a concise report containing:

- `Month` and `Status`: use `COMPLETE`, `COMPLETE WITH SMELLS`, or `INCOMPLETE`.
- `AgentBridge`: configured URL/port, Codex model ID, and whether this run started it.
- `Extraction`: new, duplicate, failed, and target-month processed/export document counts.
- `Reconciliation`: one line per statement with account/source, reconciled/total, rate, incomplete, and unmatched counts.
- `Verification`: auditor result and groundtruth regression result or `not present`.
- `Smells`: evidence-backed findings ordered by severity, or `None found`.
- `Repairs`: metadata, policy, code, or AgentBridge changes made and the validation run for them.
- `Evidence`: month export path and pipeline log path.

Use `INCOMPLETE` when no bank statement was exported, no transactions were found, a reconciliation sidecar is missing/stale/invalid, or any statement is below 100%. Do not hide unresolved smells behind the overall reconciliation percentage.

## Bundled resource

- `scripts/audit_month.py`: read-only JSON audit of AgentBridge/Codex configuration, target-month extraction metadata, and reconciliation completeness.
