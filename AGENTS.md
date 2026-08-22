# papertrail repository contract

`papertrail` is a Python 3.12+ CLI for ingesting, classifying, renaming, exporting,
and reconciling PDFs, images, and supported bank-statement XLSX files.

## Authoritative surfaces

- CLI entrypoint and options: `main.py` (`papertrail = "main:app"` in `pyproject.toml`)
- Typed profile schema and loading: `papertrail/config.py`
- Example profile: `profile.yaml.example`
- Command orchestration: `papertrail/commands/__init__.py`
- Reconciliation engine: `papertrail/commands/reconcile.py`
- Bundled reconciliation policy: `papertrail/reconciliation_policy.yaml`
- Metadata models: `papertrail/models.py`
- Document persistence and filename repair: `papertrail/repository.py`
- User-facing setup and commands: `README.md`

Search these live surfaces before adding new defaults, commands, or compatibility
paths. Do not copy their full schemas or option lists into this file.

## Invariants

1. Preserve original extracted values in `document_type_raw` and
   `issuing_party_raw` so documents can be re-normalized later.
2. Deduplicate by `hash_content`, but derive filenames from `hash_file`. XLSX
   files use `hash_file_fast` for both because they cannot be pixel-rendered.
3. Use `$UNKNOWN$` as the only fallback for unrecognized normalized values.
4. Successful QR extraction has 100% confidence and overrides corresponding LLM
   values during merge.
5. Sidecar JSON is authoritative metadata. Filenames are derived from sidecars;
   `rename` repairs disagreements.

## Runtime architecture

- `pipeline` runs ingest, classification, export, and reconciliation in order.
- PDFs and converted images use the vision classification path in
  `papertrail/engine.py` and `papertrail/llm.py`.
- XLSX bank statements are classified deterministically by parsers registered in
  `papertrail/bank_statement/extractor.py`.
- Portuguese invoice QR extraction lives in `papertrail/qr/`. Multi-QR PDFs keep
  the wrapper classification on the parent and store independent entries in
  `sub_documents`.
- Processed sidecars are the source of known document types and issuing parties;
  there is no fixed canonical enum beyond `$UNKNOWN$`.
- Reconciliation rules are first-match-wins and configurable through the profile
  `reconciliation` section or its `policy_files` overlays.
- Reconciliation writes `.reconciliation.json` beside each source statement and
  never modifies the XLSX input.

## Profiles and state

Profiles live at `~/.config/papertrail/profiles/<name>/profile.yaml` and are
validated as Pydantic models from `papertrail/config.py`.

- Cache: `~/.config/papertrail/cache/`
- Gmail credentials: `~/.config/papertrail/credentials/`
- Task logs: `{processed}/logs/`

Use `profile.yaml.example` as the repository-owned schema example. Keep
business-specific aliases and reconciliation policy in external profile files,
not in the bundled generic policy.

## Development rules

- Work on the current branch unless the user asks for another branch.
- Preserve unrelated worktree changes.
- Use `tqdm` or the runtime console tracker for file loops.
- Route task logs through the command-layer logging context.
- Log document failures through the existing failure logger.
- Use Pydantic validators for metadata normalization.
- Keep structured LLM output deterministic (`tool_choice`, temperature 0).
- Add a bank format by implementing a parser and registering it in
  `papertrail/bank_statement/extractor.py`.
- Add a QR format in `papertrail/qr/` without weakening QR-over-LLM precedence.

## Validation

Run the test suite with:

```bash
uv run --frozen --extra dev pytest -q
```

Every code change must also pass:

```bash
make regression-golden
```

The golden regression checks the approved reconciliation exports for `2026-01`
through `2026-04`. Treat failures as regressions unless the user explicitly asks
to update approvals. Run `make regression-seed-golden` only when the user asks to
seed or refresh the baseline.

For focused reconciliation work, run the relevant `tests/test_reconcile_*.py`,
`tests/test_reconciliation_*.py`, and `tests/test_rules.py` files before the full
suite and golden regression.

## Local tools

The Gradio tools are active supported surfaces:

- `python tools/browse.py --port auto`
- `python tools/dedupe.py --port auto`
- `python tools/review.py --port auto`
- `python tools/app.py --port auto` for the unified tabbed app

The `papertrail review` command launches the reconciliation review tool. Preserve
the standalone launchers and the unified app unless a product decision explicitly
retires one of those modes.

## Project skills

- Use `/extract-last-month` from
  `.agents/skills/extract-last-month/SKILL.md` for recurring previous-month
  ingestion, AgentBridge + Codex classification, reconciliation completion, data-smell
  triage, and the final monthly status report.
