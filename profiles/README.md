# Configuration Profiles

Profile-based configuration system for managing multiple papertrail environments (personal, work, testing, etc.) with a self-contained folder per environment.

## Quick Start

1. **Create a profile from a template:**
   ```bash
   mkdir profiles/default
   cp profiles/profile.yaml.example profiles/default/profile.yaml
   cp profiles/mappings.yaml.example profiles/default/mappings.yaml
   ```

2. **Edit the profile with your settings:**
   ```bash
   vim profiles/default/profile.yaml
   ```

3. **Run papertrail with your profile:**
   ```bash
   papertrail --profile default extract_new /path/to/processed
   ```

## Directory Structure

Each profile is a self-contained folder under `profiles/`:

```
profiles/
  profile.yaml.example         # Template for new profiles
  mappings.yaml.example         # Template for mappings
  README.md                     # This file
  default/                      # "default" profile
    profile.yaml                # Profile configuration
    mappings.yaml               # Raw -> canonical normalization mappings
    rejected_values.yaml        # Rejected normalizations (auto-generated)
    qr_inventory.yaml           # QR code inventory (auto-generated)
    qr_inventory.checkpoint.yaml
  personal/                     # "personal" profile (example)
    profile.yaml
    mappings.yaml
    ...
```

Cross-profile caches live in `.cache/` at repo root:

```
.cache/
  hash_cache.yaml               # File hash -> content hash cache
  nif_cache.yaml                # NIF -> issuer name cache
  .extract.lock                 # Extraction lock file
```

## Credentials Storage

**Recommended**: Store sensitive credentials in `.credentials/` (gitignored directory in the repo)

Gmail credentials and tokens are stored there by default:
- `.credentials/gmail_credentials.json` - OAuth2 client credentials (download from Google Cloud Console)
- `.credentials/gmail_token.json` - Auto-generated refresh token (created on first authentication)

In your profile, set Gmail credentials to `null` to use the defaults:

```yaml
gmail:
  enabled: true
  credentials_file: null  # Defaults to ../.credentials/gmail_credentials.json
  token_file: null  # Defaults to ../.credentials/gmail_token.json
```

## Profile File Structure

Profiles are YAML files with the following structure:

```yaml
profile:
  name: "profile-name"
  description: "Profile description"

paths:
  raw: ["/path/to/raw"]
  processed: "/path/to/processed"
  export: "/path/to/export"

openrouter:
  model_id: "google/gemini-2.5-flash"
  api_key: "${OPENROUTER_API_KEY}"
  base_url: "https://openrouter.ai/api/v1"

document_types:
  predefined: null

issuing_parties:
  predefined: null

gmail:
  enabled: true
  credentials_file: null
  token_file: null
  settings:
    attachment_mime_types: ["application/pdf"]
    label_filter: null
    max_results_per_query: 500
    skip_already_downloaded: true

passwords:
  passwords:
    - "password1"

validations:
  rules:
    - document_type: "invoice"
      issuing_party: "Amazon"

pipeline:
  tools_required: []
  default_export_date: "last_month"

task_defaults: {}
```

## Field Reference

### `profile` (Required)

Metadata about the profile.

- **`name`** (string, required): Profile identifier
- **`description`** (string, optional): Human-readable description

### `paths` (Required)

Directory paths for document processing.

- **`raw`** (list of strings): Directories to scan for new documents. Multiple paths can be specified.
  ```yaml
  raw:
    - "/Users/me/Downloads"
    - "/Users/me/Documents/Inbox"
  ```

- **`processed`** (string): Directory where processed documents are stored with metadata
- **`export`** (string): Directory for exported documents

**Path Resolution:**
- Absolute paths (starting with `/` or drive letter) are used as-is
- Relative paths (like `../config/`) are resolved relative to the profile file location

### `openrouter` (Required)

OpenRouter API configuration for LLM-based classification.

- **`model_id`** (string): Model identifier (e.g., `"google/gemini-2.5-flash"`, `"openai/gpt-4.1"`)
- **`api_key`** (string): API key. Use `${VAR}` syntax to reference environment variables (recommended for security)
- **`base_url`** (string): API base URL (default: `"https://openrouter.ai/api/v1"`)

**Environment Variable Expansion:**
```yaml
api_key: "${OPENROUTER_API_KEY}"  # References $OPENROUTER_API_KEY from .env or environment
```

### `document_types` (Optional)

Configuration for document type classification.

- **`predefined`** (list or null): Predefined list of document types to use
  - Set to `null` (recommended) to dynamically load from processed metadata
  - Set to a list to use only specific types:
    ```yaml
    predefined:
      - "invoice"
      - "receipt"
      - "statement"
      - "$UNKNOWN$"
    ```

- **`fallback_file`** (string): Path to JSON file with fallback document types
- **`fallback_list`** (list): Hardcoded fallback list (alternative to fallback_file)

### `issuing_parties` (Optional)

Configuration for issuing party (vendor/organization) classification.

- **`predefined`** (list or null): Predefined list of issuing parties
  - Set to `null` (recommended) to dynamically load from processed metadata

- **`fallback_list`** (list): Hardcoded fallback list

### `gmail` (Optional)

Gmail API integration for downloading email attachments.

- **`enabled`** (bool): Enable Gmail integration
- **`credentials_file`** (string or null): Path to OAuth2 client credentials JSON
  - Set to `null` (recommended) to use default: `../.credentials/gmail_credentials.json`
- **`token_file`** (string or null): Path to store/load OAuth2 refresh token
  - Set to `null` (recommended) to use default: `../.credentials/gmail_token.json`
- **`settings`** (object):
  - **`attachment_mime_types`** (list): MIME types to download (default: `["application/pdf"]`)
  - **`label_filter`** (string or null): Gmail label to filter by (e.g., `"Bills"`)
  - **`max_results_per_query`** (int): Max messages per query (default: `500`)
  - **`skip_already_downloaded`** (bool): Skip already downloaded attachments (default: `true`)

### `passwords` (Optional)

Password configuration for ZIP extraction.

**Inline passwords (recommended)**:
```yaml
passwords:
  passwords:
    - "password1"
    - "password2"
```

### `validations` (Optional)

File validation schema configuration.

**Inline rules (recommended)**:
```yaml
validations:
  rules:
    - document_type: "invoice"
      issuing_party: "Amazon"
    - document_type: "receipt"
```

### `pipeline` (Optional)

Pipeline task configuration.

- **`tools_required`** (list): List of required external tools
- **`default_export_date`** (string): Default export date for pipeline (e.g., `"last_month"`)

### `task_defaults` (Optional)

Task-specific default settings (reserved for future use).

## Using Profiles

### Selecting a Profile

Use the `--profile` flag to select a profile:

```bash
# Use the 'default' profile
papertrail --profile default extract_new /path/to/processed

# Use the 'personal' profile
papertrail --profile personal pipeline

# Use the 'work' profile
papertrail --profile work export_excel /path/to/processed --excel_output_path output.xlsx
```

### Auto-Detection

If `--profile` is not specified:
1. If a `default` profile exists (i.e. `profiles/default/profile.yaml`), it will be used automatically
2. If no `default` profile exists, an error is raised listing available profiles

### Multiple Environments

Example setup for personal and work:

```
profiles/
  default/
    profile.yaml        # Auto-loaded if --profile not specified
    mappings.yaml
  personal/
    profile.yaml        # Personal documents
    mappings.yaml
  work/
    profile.yaml        # Work documents
    mappings.yaml
```

Switch between them:

```bash
papertrail --profile personal pipeline
papertrail --profile work pipeline --export_date 2025-01
```

## Creating Profiles

### From Templates

Create a new profile directory and copy templates:

```bash
# Default profile
mkdir profiles/default
cp profiles/profile.yaml.example profiles/default/profile.yaml
cp profiles/mappings.yaml.example profiles/default/mappings.yaml
vim profiles/default/profile.yaml

# Personal profile
mkdir profiles/personal
cp profiles/profile.yaml.example profiles/personal/profile.yaml
cp profiles/mappings.yaml.example profiles/personal/mappings.yaml
vim profiles/personal/profile.yaml
```

## Configuration Precedence

Settings are resolved in the following order (highest priority first):

1. **CLI arguments** (e.g., `--raw_path`) - always override everything
2. **Environment variables** (for `${VAR}` expansion in profiles)
3. **Active profile** (selected via `--profile`)
4. **Default profile** (if no `--profile` specified and `profiles/default/profile.yaml` exists)

## Troubleshooting

### Profile Not Found

```
Error: Profile 'work' not found at profiles/work/profile.yaml.
Available profiles: default, personal
```

**Solution:** Create the profile directory with a `profile.yaml` inside.

### Missing Required Field

```
Error: Profile 'personal' missing required field: paths.processed
```

**Solution:** Add the required field to your profile YAML.

### YAML Parse Error

```
Error: Failed to parse profile 'personal'
```

**Solution:** Fix the YAML syntax error. Common issues:
- Missing quotes around strings with special characters
- Incorrect indentation (use spaces, not tabs)
- Missing colons after keys

## Additional Resources

- [Main README](../README.md) - General papertrail documentation
- [CLAUDE.md](../CLAUDE.md) - Development context
