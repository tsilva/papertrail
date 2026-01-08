#!/usr/bin/env python3
"""
Utility to check file hash and content hash for a PDF file.
"""

import argparse
import sys
from pathlib import Path

from documentor.hashing import hash_file_fast, hash_file_content


def main():
    parser = argparse.ArgumentParser(description="Check file hash and content hash for a PDF file.")
    parser.add_argument("file_path", type=str, help="Path to the PDF file")
    args = parser.parse_args()

    path = Path(args.file_path)

    if not path.exists():
        print(f"Error: File not found: {path}")
        sys.exit(1)

    print(f"File: {path}")
    print(f"File hash (fast):    {hash_file_fast(path)}")
    print(f"Content hash:        {hash_file_content(path)}")


if __name__ == "__main__":
    main()
