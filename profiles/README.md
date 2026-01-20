# Configuration Profiles

Profile-based configuration system for managing multiple Documentor environments (personal, work, testing, etc.) with a single YAML file per environment.

## Quick Start

1. **Create a profile from a template:**
   ```bash
   cp default.yaml.example default.yaml
   ```

2. **Edit the profile with your settings:**
   ```bash
   vim default.yaml  # or use your favorite editor
   ```

3. **Run Documentor with your profile:**
   ```bash
   documentor --profile default extract_new /path/to/processed
   ```

## Credentials Storage

**Recommended**: Store sensitive credentials in `config/` (gitignored directory in the repo)

Gmail credentials and tokens are stored in `config/` by default:
- `config/gmail_credentials.json` - OAuth2 client credentials (download from Google Cloud Console)
- `config/gmail_token.json` - Auto-generated refresh token (created on first authentication)

In your profile, set Gmail credentials to `null` to use the default `config/` location:

```yaml
gmail:
  enabled: true
  credentials_file: null  # Defaults to ../config/gmail_credentials.json
  token_file: null  # Defaults to ../config/gmail_token.json
```

**Why**: Keeping credentials in `config/` keeps everything in the repo structure while staying gitignored. The `config/` directory is already configured to exclude sensitive files.

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
  fallback_file: "../config/document_types.json"

issuing_parties:
  predefined: null

gmail:
  enabled: true
  credentials_file: "../config/gmail_credentials.json"
  token_file: "../config/gmail_token.json"
  settings:
    attachment_mime_types: ["application/pdf"]
    label_filter: null
    max_results_per_query: 500
    skip_already_downloaded: true

config_files:
  passwords: "../config/passwords.txt"
  validations: "../config/file_check_validations.json"

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

- **`fallback_file`** (string): Path to JSON file with fallback document types (used if processed directory doesn't exist)
- **`fallback_list`** (list): Hardcoded fallback list (alternative to fallback_file)

### `issuing_parties` (Optional)

Configuration for issuing party (vendor/organization) classification.

- **`predefined`** (list or null): Predefined list of issuing parties
  - Set to `null` (recommended) to dynamically load from processed metadata
  - Set to a list to use only specific parties

- **`fallback_list`** (list): Hardcoded fallback list

### `gmail` (Optional)

Gmail API integration for downloading email attachments.

- **`enabled`** (bool): Enable Gmail integration
- **`credentials_file`** (string or null): Path to OAuth2 client credentials JSON
  - Set to `null` (recommended) to use default: `../config/gmail_credentials.json`
  - Or specify custom path: `"/path/to/credentials.json"`
- **`token_file`** (string or null): Path to store/load OAuth2 refresh token
  - Set to `null` (recommended) to use default: `../config/gmail_token.json`
  - Or specify custom path: `"/path/to/token.json"`
- **`settings`** (object):
  - **`attachment_mime_types`** (list): MIME types to download (default: `["application/pdf"]`)
  - **`label_filter`** (string or null): Gmail label to filter by (e.g., `"Bills"`)
  - **`max_results_per_query`** (int): Max messages per query (default: `500`)
  - **`skip_already_downloaded`** (bool): Skip already downloaded attachments (default: `true`)

**Recommended setup**: Set both fields to `null` in your profile to use the gitignored `config/` directory

### `passwords` (Optional)

Password configuration for ZIP extraction.

**Inline passwords (recommended)**:
```yaml
passwords:
  passwords:
    - "password1"
    - "password2"
    - "archive-password"
```

**External file (legacy)**:
```yaml
passwords:
  passwords_file: "../config/passwords.txt"
```

- **`passwords`** (list of strings): Inline list of passwords to try when extracting password-protected ZIPs
- **`passwords_file`** (string): Path to external password file (one password per line)

**Note**: Inline passwords are recommended for simplicity. All passwords are stored in the profile YAML, which should be gitignored.

### `validations` (Optional)

File validation schema configuration.

**Inline rules (recommended)**:
```yaml
validations:
  rules:
    - document_type: "invoice"
      issuing_party: "Amazon"
      service_name: "AWS"
    - document_type: "receipt"
      issuing_party: "Vendor Name"
    - document_type: "bank-statement"  # Any field can be specified
```

**External file (legacy)**:
```yaml
validations:
  validations_file: "../config/file_check_validations.json"
```

- **`rules`** (list of objects): Inline validation rules. Each rule can specify any combination of `document_type`, `issuing_party`, `service_name`, etc.
- **`validations_file`** (string): Path to external JSON validation schema file

**Note**: Inline rules are recommended for simplicity and self-documentation.

### `pipeline` (Optional)

Pipeline task configuration.

- **`tools_required`** (list): List of required external tools
- **`default_export_date`** (string): Default export date for pipeline (e.g., `"last_month"`)

### `task_defaults` (Optional)

Task-specific default settings (reserved for future use).

## Environment Variable Expansion

Profiles support environment variable expansion using `${VAR}` syntax:

```yaml
openrouter:
  api_key: "${OPENROUTER_API_KEY}"
  model_id: "${OPENROUTER_MODEL_ID:-google/gemini-2.5-flash}"  # Not yet supported: default values
```

**Best Practices:**
- Store sensitive data (API keys) in environment variables, not in profile files
- Create a `.env` file at the repo root with your secrets:
  ```env
  OPENROUTER_API_KEY=sk-or-v1-...
  ```
- Reference the env vars in your profile using `${VAR}` syntax

## Using Profiles

### Selecting a Profile

Use the `--profile` flag to select a profile:

```bash
# Use the 'default' profile
documentor --profile default extract_new /path/to/processed

# Use the 'personal' profile
documentor --profile personal pipeline

# Use the 'work' profile
documentor --profile work export_excel /path/to/processed --excel_output_path output.xlsx
```

### Auto-Detection

If `--profile` is not specified:
1. If a `default.yaml` profile exists, it will be used automatically
2. If no `default.yaml` exists, legacy `.env` configuration is used
3. If multiple profiles exist but no `default.yaml`, a warning is printed and legacy mode is used

### Legacy .env Mode

Profiles are optional. If no profiles exist or you don't specify `--profile`, Documentor falls back to the legacy `.env` configuration system.

## Creating Profiles

### From Templates

Copy an example template and customize:

```bash
# Default profile
cp default.yaml.example default.yaml
vim default.yaml

# Personal profile
cp personal.yaml.example personal.yaml
vim personal.yaml

# Work profile
cp work.yaml.example work.yaml
vim work.yaml
```

### Multiple Environments

Example setup for personal and work:

```
profiles/
├── default.yaml        # Auto-loaded if --profile not specified
├── personal.yaml       # Personal documents
├── work.yaml          # Work documents
└── test.yaml          # Testing/development
```

Switch between them:

```bash
documentor --profile personal pipeline
documentor --profile work pipeline --export_date 2025-01
documentor --profile test extract_new /tmp/test-processed --raw_path /tmp/test-raw
```

## Configuration Precedence

Settings are resolved in the following order (highest priority first):

1. **CLI arguments** (e.g., `--raw_path`) - always override everything
2. **Environment variables** (for `${VAR}` expansion in profiles)
3. **Active profile** (selected via `--profile`)
4. **Default profile** (if no `--profile` specified and `default.yaml` exists)
5. **Legacy .env** (if no profiles exist)

## Dynamic vs Predefined Enums

### Dynamic Loading (Recommended)

Set `predefined: null` to dynamically load values from processed metadata:

```yaml
document_types:
  predefined: null  # Scans processed/*.json files for unique document_type values
```

**Benefits:**
- Automatically discovers new document types as you process documents
- No manual updates needed
- Always up-to-date with your actual data

### Predefined Lists

Set `predefined: [...]` to use a fixed list:

```yaml
document_types:
  predefined:
    - "invoice"
    - "receipt"
    - "statement"
    - "$UNKNOWN$"
```

**Use cases:**
- Strict classification requirements
- Testing with limited types
- Preventing accidental new types

## Troubleshooting

### Profile Not Found

```
Error: Profile 'work' not found at profiles/work.yaml
Available profiles: default, personal
```

**Solution:** Create the profile or use an existing one.

### Missing Required Field

```
Error: Profile 'personal' missing required field: paths.processed
```

**Solution:** Add the required field to your profile YAML.

### Undefined Environment Variable

```
Error: Profile 'personal' references undefined variable: OPENROUTER_API_KEY
```

**Solution:** Set the environment variable in your `.env` file or shell:
```bash
echo 'OPENROUTER_API_KEY=sk-or-v1-...' >> .env
```

### YAML Parse Error

```
Error: Failed to parse profile 'personal'
  File: profiles/personal.yaml
  Line 15: mapping values are not allowed here
```

**Solution:** Fix the YAML syntax error. Common issues:
- Missing quotes around strings with special characters
- Incorrect indentation (use spaces, not tabs)
- Missing colons after keys

## Migration from .env

To migrate from legacy `.env` to profiles:

1. **Create a default profile:**
   ```bash
   cp profiles/default.yaml.example profiles/default.yaml
   ```

2. **Copy values from .env to profile:**
   - `RAW_FILES_DIR` → `paths.raw` (convert semicolon-separated to list)
   - `PROCESSED_FILES_DIR` → `paths.processed`
   - `EXPORT_FILES_DIR` → `paths.export`
   - `OPENROUTER_MODEL_ID` → `openrouter.model_id`
   - `OPENROUTER_API_KEY` → Keep in `.env`, reference as `"${OPENROUTER_API_KEY}"`

3. **Test the profile:**
   ```bash
   documentor --profile default validate_metadata /path/to/processed
   ```

4. **Keep .env for secrets:**
   ```env
   # .env
   OPENROUTER_API_KEY=sk-or-v1-...
   ```

5. **(Optional) Remove old .env settings:**
   Once you've verified the profile works, you can remove non-secret settings from `.env`.

## Examples

### Personal Documents Profile

```yaml
profile:
  name: "personal"
  description: "Personal documents on local drive"

paths:
  raw: ["/Users/me/Downloads", "/Users/me/Desktop/Takeout"]
  processed: "/Users/me/Documents/Processed"
  export: "/Users/me/Documents/Export"

openrouter:
  model_id: "google/gemini-2.5-flash"
  api_key: "${OPENROUTER_API_KEY}"
  base_url: "https://openrouter.ai/api/v1"

document_types:
  predefined: null  # Dynamic loading

issuing_parties:
  predefined: null  # Dynamic loading

gmail:
  enabled: true
  credentials_file: null  # Defaults to ../config/gmail_credentials.json
  token_file: null  # Defaults to ../config/gmail_token.json
  settings:
    attachment_mime_types: ["application/pdf"]
    label_filter: "Personal/Bills"
    max_results_per_query: 500
    skip_already_downloaded: true

# Inline passwords (recommended)
passwords:
  passwords:
    - "my-personal-password"

# Inline validation rules (recommended)
validations:
  rules:
    - document_type: "invoice"
      issuing_party: "Electric Company"
    - document_type: "bank-statement"

pipeline:
  tools_required: ["mbox-extractor", "archive-extractor", "pdf-merger"]
  default_export_date: "last_month"

task_defaults: {}
```

### Work Documents Profile

```yaml
profile:
  name: "work"
  description: "Work documents on Google Drive"

paths:
  raw: ["/Users/me/Work/Inbox"]
  processed: "/Users/me/Google Drive/Work/Processed"
  export: "/Users/me/Google Drive/Work/Export"

openrouter:
  model_id: "openai/gpt-4.1"
  api_key: "${OPENROUTER_API_KEY_WORK}"
  base_url: "https://openrouter.ai/api/v1"

document_types:
  # Restricted to business types
  predefined:
    - "invoice"
    - "receipt"
    - "contract"
    - "statement"
    - "$UNKNOWN$"

issuing_parties:
  predefined: null  # Dynamic loading

gmail:
  enabled: true
  credentials_file: null  # Defaults to ../config/gmail_credentials.json
  token_file: null  # Defaults to ../config/gmail_token.json
  settings:
    attachment_mime_types: ["application/pdf"]
    label_filter: "Work/Invoices"
    max_results_per_query: 500
    skip_already_downloaded: true

# Inline passwords (recommended)
passwords:
  passwords:
    - "work-archive-password"

# Inline validation rules (recommended)
validations:
  rules:
    - document_type: "invoice"
      issuing_party: "Amazon Business"
    - document_type: "contract"
    - document_type: "purchase-order"

pipeline:
  tools_required: ["mbox-extractor", "archive-extractor", "pdf-merger"]
  default_export_date: "current_month"

task_defaults: {}
```

## Additional Resources

- [Main README](../README.md) - General Documentor documentation
- [CLAUDE.md](../CLAUDE.md) - Development context
- [.env.example](../.env.example) - Legacy configuration example
