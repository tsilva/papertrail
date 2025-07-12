from pathlib import Path
import hashlib
import sys

def hash_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b''): h.update(chunk)
    return h.hexdigest()[:8]

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python hash.py <file_path>")
        sys.exit(1)
    file_path = Path(sys.argv[1])
    print(hash_file(file_path))