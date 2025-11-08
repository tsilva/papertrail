#!/usr/bin/env python3
"""Test script for two-phase extraction on the problematic file."""

import sys
from pathlib import Path

# Import from main.py
sys.path.insert(0, str(Path(__file__).parent))
from main import classify_pdf_document, hash_file

# Test file
test_pdf = Path("/Users/tsilva/Google Drive/My Drive/documentor-puzzle/processed/2025-10-09 - invoice - $unknown$ - claude pro - 1800 eur - f5079cfb.pdf")

print("Testing two-phase extraction on problematic file...")
print(f"File: {test_pdf.name}\n")

# Calculate hash
file_hash = hash_file(test_pdf)
print(f"Hash: {file_hash}\n")

# Run classification
try:
    metadata = classify_pdf_document(test_pdf, file_hash)

    print("=" * 60)
    print("EXTRACTION RESULTS")
    print("=" * 60)
    print(f"\nRAW VALUES (Phase 1 - Extracted):")
    print(f"  document_type: {metadata.document_type_raw}")
    print(f"  issuing_party: {metadata.issuing_party_raw}")

    print(f"\nNORMALIZED VALUES (Phase 2 - Mapped):")
    print(f"  document_type: {metadata.document_type}")
    print(f"  issuing_party: {metadata.issuing_party}")

    print(f"\nOTHER METADATA:")
    print(f"  issue_date: {metadata.issue_date}")
    print(f"  service_name: {metadata.service_name}")
    print(f"  total_amount: {metadata.total_amount} {metadata.total_amount_currency}")
    print(f"  confidence: {metadata.confidence}")

    print(f"\nREASONING:")
    print(f"  {metadata.reasoning}")

    print("\n" + "=" * 60)

    if metadata.issuing_party != "$UNKNOWN$":
        print("✅ SUCCESS: Issuing party was correctly normalized!")
    else:
        print("❌ FAILED: Issuing party is still $UNKNOWN$")

except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()
