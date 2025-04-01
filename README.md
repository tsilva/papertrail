# documentor 📜

<p align="center">
  <img src="logo.png" alt="documentor logo" width="400"/>
</p>

A document classification and organization tool that uses Claude AI to automatically categorize, extract metadata, and rename PDF documents. 🚀

## Features ✨

- **Intelligent Document Classification** 🧠: Uses Claude AI to classify document types and extract metadata
- **Automated Renaming** ✏️: Renames files based on extracted information (date, document type, issuer, etc.)
- **Metadata Extraction** 📋: Pulls out key information like dates, amounts, and issuers
- **Deduplication** 🕵️: Identifies duplicate files using SHA-256 hashing
- **Validation** ✅: Verifies metadata integrity and file consistency
- **CSV Export** 📊: Creates spreadsheet summaries of document collections

## Requirements ⚙️

- Python 3.8+
- Anthropic API key for Claude 🔑

## Installation 🛠️

```bash
git clone https://github.com/tsilva/documentor.git
cd github-bootstrapper
curl -L https://gist.githubusercontent.com/tsilva/258374c1ba2296d8ba22fffbf640f183/raw/venv-install.sh -o install.sh && chmod +x install.sh && ./install.sh
```

```bash
curl -L https://gist.githubusercontent.com/tsilva/8588cb367242e3db8f1b33c42e4e5e06/raw/venv-run.sh -o run.sh && chmod +x run.sh && ./run.sh
```

## Usage 🎯

The tool offers several operations:

### Extract Metadata from PDFs 📤

Process new PDFs, classify them, and save with descriptive filenames:

```bash
python main.py extract /path/to/pdfs
```

### Rename Existing Files 🔄

Update filenames of already processed documents based on their metadata:

```bash
python main.py rename /path/to/output/directory
```

### Validate Metadata ✔️

Check for consistency between metadata and files:

```bash
python main.py validate /path/to/output/directory
```

### Export to CSV 📈

Generate a spreadsheet of all document metadata:

```bash
python main.py csv /path/to/output/directory
```

## Configuration 🖌️

Document types are defined in `config/document_types.json`. This file determines valid classification categories.

## How It Works 🤓

1. PDFs are scanned and hashed to identify unique documents 🔍
2. The first page is rendered as an image and sent to Claude AI 🖼️
3. Claude extracts structured metadata (dates, amounts, document types) 📝
4. Files are renamed using a consistent format: `date - type - issuer - [service] - [amount] - hash.pdf` 📛
5. Metadata is stored alongside each PDF as a JSON file 💾

## File Naming Convention 📏

Files are renamed using this format:
```
YYYY-MM-DD - documenttype - issuer - [service] - [amount currency] - hash.pdf
```

Example:
```
2023-04-15 - invoice - amazon - aws services - 29.99 eur - a1b2c3d4.pdf
```