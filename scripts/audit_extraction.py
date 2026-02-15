#!/usr/bin/env python3
"""Extraction quality audit — standalone wrapper.

Usage:
    python scripts/audit_extraction.py <processed_path>

Prefer: python main.py --profile default audit
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from papertrail.tasks.audit import task_audit


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <processed_path>")
        sys.exit(1)

    processed_path = Path(sys.argv[1])
    if not processed_path.is_dir():
        print(f"Error: {processed_path} is not a directory")
        sys.exit(1)

    task_audit(processed_path)


if __name__ == "__main__":
    main()
