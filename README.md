<p align="center">
  <img src="./logo.png" alt="papertrail" width="512" />
  <br />
  <!-- repo-tagline:start -->
  <strong>🧾 Sort documents into sidecar-backed order 🧾</strong>
  <!-- repo-tagline:end -->
</p>

papertrail is a Python CLI for classifying, deduplicating, renaming, exporting, and reconciling personal or business documents. By default, it handles PDFs with Codex through a local AgentBridge server, converts image files to PDF, and parses supported bank-statement XLSX exports deterministically.

The processed folder contains the document files and authoritative `.json` sidecars. Filenames are derived from sidecar metadata, while `sync`, `check`, and `rename` repair the collection when metadata changes.

## Install

papertrail requires Python 3.12+, `uv`, AgentBridge, and an authenticated Codex CLI for LLM classification.

```bash
git clone https://github.com/tsilva/papertrail.git
cd papertrail
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e '.[dev]'
uv tool install agentbridge-cli
codex login
mkdir -p ~/.config/papertrail/profiles/personal
cp profile.yaml.example ~/.config/papertrail/profiles/personal/profile.yaml
```

Edit `~/.config/papertrail/profiles/personal/profile.yaml` with your raw, processed, and export paths. Start AgentBridge in one terminal, then run the pipeline in another:

```bash
agentbridge
```

```bash
papertrail --profile personal pipeline
```

The historical `openrouter:` profile key configures the OpenAI-compatible LLM endpoint. Its defaults are `http://127.0.0.1:8082/api/v1`, `codex/gpt-6.1-sol` with high reasoning, and the AgentBridge placeholder key `not-needed`. Configure the AgentBridge endpoint and model through this profile section; upstream provider credentials are managed by AgentBridge. `AGENTBRIDGE_BASE_URL` and `AGENTBRIDGE_API_KEY` override the local gateway settings.

## Commands

```bash
papertrail --profile personal pipeline                         # ingest, classify, export, reconcile
papertrail --profile personal extract                          # process new raw PDFs, images, and XLSX files
papertrail --profile personal sync --all-unknown               # reprocess unknown metadata
papertrail --profile personal check --verify-hashes            # audit metadata and hashes
papertrail --profile personal rename                           # rename files from sidecar JSON
papertrail --profile personal reconcile                        # reconcile exported documents to bank rows
papertrail --profile personal review                           # open reconciliation review UI
papertrail --profile personal regression --export-date 2026-04 # check approved reconciliation groundtruth
papertrail --profile personal gmail --months 2                 # download Gmail attachments
papertrail --profile personal gmail-auth                       # reconnect Gmail in the browser
papertrail --profile personal export excel --output /tmp/docs.xlsx
papertrail --profile personal export dates --base-dir /tmp/export
papertrail --profile personal export copy --pattern 2026-01 --dest /tmp/january
uv run --frozen --extra dev pytest -q                          # run tests
make regression-golden PROFILE=personal                         # run golden reconciliation months
```

## Notes

- Profiles live under `~/.config/papertrail/profiles/<name>/profile.yaml`.
- Caches live under `~/.config/papertrail/cache/`; the Gmail file backend uses `~/.config/papertrail/credentials/`. The Infisical backend keeps OAuth state in Infisical.
- Logs are written to `{processed}/logs/` for extraction, sync, pipeline, and classification failures.
- Reconciliation policy defaults can be overridden in `profile.yaml` under `reconciliation`.
- QR extraction requires the `pyzbar` package and the system `zbar` library.
- Duplicate detection uses `hash_content`; filenames use `hash_file`, so visually identical PDFs can still keep distinct byte-based names.
- `$UNKNOWN$` is the fallback sentinel for unrecognized metadata.
- Sidecar JSON is the source of truth. If a filename and sidecar disagree, `rename` fixes the filename.
- Local Gradio tools can be run directly with `python tools/browse.py`, `python tools/dedupe.py`, and `python tools/review.py`.

## Architecture

### Gmail credentials in Infisical

Install and log into the Infisical CLI, then store the OAuth client configuration
and authorized-user token JSON in a dedicated project. For an existing file-based
login, upload the files without placing their values in command arguments:

```bash
infisical secrets set --projectId <project-id> --env prod \
  PAPERTRAIL_GMAIL_CLIENT_JSON=@"$HOME/.config/papertrail/credentials/gmail_credentials.json" \
  PAPERTRAIL_GMAIL_TOKEN_JSON=@"$HOME/.config/papertrail/credentials/gmail_token.json"
```

Select the backend in your profile:

```yaml
gmail:
  enabled: true
  credential_backend: infisical
  infisical:
    project_id: <project-id>
    environment: prod
    path: /
```

Run `papertrail` normally. It retrieves the selected OAuth JSON through the CLI
and writes refreshed authorization back to the token secret through stdin. The
login needs read permission on both secrets and write permission on the token.
`login_profile`, `executable`, and the secret names can be overridden as shown in
`profile.yaml.example`. CLI failures stop authentication without falling back to
local credential files. Verify Gmail access and vault writeback before removing
the old files. The default `file` backend remains available for existing profiles;
it writes refreshed tokens atomically with owner-only permissions.

If Google rejects a saved refresh token, run
`papertrail --profile personal gmail-auth`. This explicitly opens Google consent
and saves the new authorization to the selected backend without downloading
attachments. It also supports first-time Infisical login after uploading only
the client configuration. Choose the Gmail account intended for this profile.

![papertrail architecture diagram](./architecture.png)

## License

[MIT](LICENSE)

## AgentBridge routing

Model requests use only AgentBridge. Set `AGENTBRIDGE_BASE_URL` to its API root
(default `http://127.0.0.1:8082/api/v1`). `AGENTBRIDGE_API_KEY` is an optional
gateway token. OpenRouter credentials belong to AgentBridge. Existing model
choices are retained; raw upstream IDs gain `openrouter/` only on outbound
requests. There is no direct OpenRouter endpoint or fallback. Hosted deployments
must configure a reachable AgentBridge URL instead of the loopback default.

## Light Svelte dashboard

`papertrail --profile personal review` opens the unified Documents and Review
screens on an available loopback port. Documents supports search, document-type
and month filters, PDF/XLSX previews, original-file downloads, and links to bank
matches. Review shows a transaction queue, source evidence, and an amount/date
comparison, with approve, reject, clear, and undo actions. Multi-document matches
can be inspected one document at a time. Missing evidence cannot be approved.

Approvals use the existing `.reconciliation.groundtruth.json` files; rejections
are stored in their `rejections` collection. Undo restores the previous decision.
Changed evidence invalidates prior decisions, and stale requests must be
refreshed before saving. The dashboard never changes extracted sidecars, source
files, or reconciliation matches. Keyboard shortcuts: `Y` approve, `N` reject,
`U` undo, and arrow keys or `j`/`k` to navigate.

The installed Python package includes the built frontend; Node.js is only needed
when editing the UI. The existing Gradio browse, dedupe, review, and unified
launchers remain available, including the expected-unmatched-document workflow:

```bash
PAPERTRAIL_UI_PROFILE=personal uv run python -m tools.dashboard --port auto
uv run python tools/app.py --port auto       # existing Gradio tools
```

To develop the Svelte frontend, start the Python dashboard for the selected
profile, then use its printed URL as the API proxy:

```bash
cd frontend
pnpm install --frozen-lockfile
PAPERTRAIL_API_URL=http://127.0.0.1:<printed-port> pnpm dev -- --port auto
pnpm check
pnpm test
pnpm build
```

The production build writes bundled assets into `tools/static/dashboard/`.
Commit updated assets alongside frontend source changes. Design references in
`frontend/design/` use fictional documents; private source files and screenshots
must stay outside the repository.
