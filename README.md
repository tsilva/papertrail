# 📄 documentor

<p align="center">
  <img src="logo.jpg" alt="logo" width="400"/>
</p>

🤖 AI-powered document classification and organization tool that automatically extracts metadata from PDFs and renames them intelligently

## 📖 Overview

Documentor uses Claude AI to analyze PDF documents, extract key metadata (dates, amounts, issuers), and organize them with consistent, descriptive filenames. It helps you manage document collections by automatically classifying document types, preventing duplicates through file hashing, and creating structured metadata that can be exported to CSV for analysis.

## 🚀 Installation

```bash
# Clone the repository
git clone https://github.com/tsilva/documentor.git
cd documentor

# Option A: Install globally with pipx
pipx install . --force

# Option B: Use uv to create a local environment
uv venv
uv pip install -r pyproject.toml
# No need to activate the venv; `uv run` handles that
uv run python main.py --help  # run the CLI directly from the repo

# (Optional) install the package locally to use the `documentor` command
uv pip install -e .
```

## 🛠️ Usage

Documentor offers several commands for document management.  
The main CLI entrypoint is:

```bash
documentor <task> <processed_path> [--raw_path ...] [--excel_output_path ...] [--regex_pattern ...] [--copy_dest_folder ...] [--check_schema_path ...] [--export_date ...]
```

### Available Tasks

- `extract_new`  
  Extract metadata from new PDFs in a raw folder and copy them to the processed folder.  
  **Usage:**  
  ```bash
  documentor extract_new <processed_path> --raw_path <raw_pdf_folder>
  ```

- `rename_files`  
  Rename existing PDF and JSON files in the processed folder based on their metadata.  
  **Usage:**  
  ```bash
  documentor rename_files <processed_path>
  ```

- `validate_metadata`  
  Validate all metadata and PDF files in the processed folder for consistency.  
  **Usage:**  
  ```bash
  documentor validate_metadata <processed_path>
  ```

- `export_excel`  
  Export all metadata in the processed folder to an Excel file.  
  **Usage:**  
  ```bash
  documentor export_excel <processed_path> --excel_output_path <output.xlsx>
  ```

- `copy_matching`  
  Copy all PDF and JSON files whose filenames match a regex pattern to a destination folder.  
  **Usage:**  
  ```bash
  documentor copy_matching <processed_path> --regex_pattern "<pattern>" --copy_dest_folder <dest_folder>
  ```

- `check_files_exist`  
  For each entry in a validation schema, check if a matching JSON file exists in the processed folder.  
  **Usage:**  
  ```bash
  documentor check_files_exist <processed_path> [--check_schema_path <schema.json>]
  ```

- `pipeline`  
  Run the full document processing pipeline (extract, rename, export, copy, merge, validate).  
  **Usage:**  
  ```bash
  documentor pipeline [--export_date YYYY-MM]
  ```

## 🔍 How It Works

1. PDFs are scanned and hashed to identify unique documents
2. The first page is rendered as an image and sent to Claude AI
3. Claude extracts structured metadata (dates, amounts, document types)
4. Files are renamed using a consistent format: `date - type - issuer - [service] - [amount] - hash.pdf`
5. Metadata is stored alongside each PDF as a JSON file

Document types are defined in `config/document_types.json`, which determines valid classification categories.

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.