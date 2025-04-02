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

# Install dependencies
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your Anthropic API key
```

## 🛠️ Usage

Documentor offers several commands for document management:

### Extract metadata from PDFs

```bash
python main.py extract /path/to/pdfs --target_path ./output/
```

### Rename existing files based on metadata

```bash
python main.py rename /path/to/output/directory
```

### Validate metadata consistency

```bash
python main.py validate /path/to/output/directory
```

### Export metadata to CSV

```bash
python main.py csv /path/to/output/directory
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