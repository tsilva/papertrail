# 📋 documentor

<p align="center">
  <img src="logo.png" alt="documentor logo" width="400"/>
</p>

> An intelligent agent that organizes your documentation repository with ease ✨

## 🚀 Overview

Documentor helps you manage your personal or business documents such as invoices, receipts, contracts, and more. Say goodbye to messy folders and hello to an organized digital life! Documentor uses hash-based tracking to ensure documents are only processed once, saving time and avoiding duplicates.

## ✨ Features

- 🔍 Search for PDF files in your system
- 📁 Automatically organize documents by type
- 📅 Sort documents by date
- 🏷️ Add custom tags to your documents
- 🔐 Secure storage for sensitive information
- 🔄 Hash-based document tracking to prevent duplicate processing
- 📂 Flat output directory for processed documents

## 🛠️ Installation

```bash
git clone https://github.com/tsilva/documentor.git
cd documentor
python main.py
```

## 🧠 How It Works

1. Documentor scans your input directory for documents
2. Each document's hash is calculated and checked against the hash registry
3. If the hash exists in the registry, the file is skipped
4. New documents are processed to determine appropriate filenames
5. Successfully processed documents are copied to the output/ folder
6. The hash registry is updated with newly processed documents
