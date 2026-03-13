"""Archive extraction helpers used by the pipeline.

This keeps archive ingestion in-process instead of depending on an external
package that is not available in the package registry.
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import shutil
import subprocess
import tarfile
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable

from papertrail.logging_utils import get_logger

logger = get_logger("archive")

_ARCHIVE_SUFFIXES = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tgz",
    ".tbz2",
    ".tbz",
    ".txz",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
)
_COMPRESSED_SUFFIXES = {".gz", ".bz2", ".xz"}


def _archive_stem(path: Path) -> str:
    lower_name = path.name.lower()
    for suffix in _ARCHIVE_SUFFIXES:
        if lower_name.endswith(suffix):
            return path.name[: -len(suffix)]
    return path.stem


def _output_dir_for(path: Path) -> Path:
    return path.parent / f"{_archive_stem(path)}_archive"


def _iter_archives(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        lower_name = path.name.lower()
        if any(lower_name.endswith(suffix) for suffix in _ARCHIVE_SUFFIXES):
            yield path


def _count_files(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file())


def _promote_tree(src: Path, dst: Path) -> int:
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return _count_files(dst)


def _extract_zip(archive_path: Path, output_dir: Path, passwords: list[str]) -> int:
    with TemporaryDirectory(dir=archive_path.parent) as temp_dir_name:
        temp_dir = Path(temp_dir_name) / "content"
        temp_dir.mkdir()
        with zipfile.ZipFile(archive_path) as zip_file:
            encrypted = any(info.flag_bits & 0x1 for info in zip_file.infolist())
            password_candidates = [None]
            if encrypted:
                password_candidates = [password.encode("utf-8") for password in passwords]
                if not password_candidates:
                    raise RuntimeError("password required for encrypted zip archive")
            for password in password_candidates:
                try:
                    zip_file.extractall(temp_dir, pwd=password)
                    return _promote_tree(temp_dir, output_dir)
                except RuntimeError:
                    shutil.rmtree(temp_dir)
                    temp_dir.mkdir()
                    continue
        raise RuntimeError("failed to extract zip archive with provided passwords")


def _extract_tar(archive_path: Path, output_dir: Path) -> int:
    with TemporaryDirectory(dir=archive_path.parent) as temp_dir_name:
        temp_dir = Path(temp_dir_name) / "content"
        temp_dir.mkdir()
        with tarfile.open(archive_path) as tar:
            tar.extractall(temp_dir, filter="data")
        return _promote_tree(temp_dir, output_dir)


def _extract_single_file(archive_path: Path, output_dir: Path) -> int:
    suffix = archive_path.suffix.lower()
    if suffix == ".gz":
        opener = gzip.open
    elif suffix == ".bz2":
        opener = bz2.open
    elif suffix == ".xz":
        opener = lzma.open
    else:
        raise RuntimeError(f"unsupported compressed file: {archive_path.name}")

    with TemporaryDirectory(dir=archive_path.parent) as temp_dir_name:
        temp_dir = Path(temp_dir_name) / "content"
        temp_dir.mkdir()
        out_path = temp_dir / _archive_stem(archive_path)
        with opener(archive_path, "rb") as src, out_path.open("wb") as dst:
            shutil.copyfileobj(src, dst)
        return _promote_tree(temp_dir, output_dir)


def _extract_with_7z(archive_path: Path, output_dir: Path, passwords: list[str]) -> int:
    if shutil.which("7z") is None:
        raise RuntimeError("7z is not installed")

    password_candidates = [None, *passwords]
    with TemporaryDirectory(dir=archive_path.parent) as temp_dir_name:
        temp_dir = Path(temp_dir_name) / "content"
        temp_dir.mkdir()
        for password in password_candidates:
            cmd = ["7z", "x", "-y", f"-o{temp_dir}", str(archive_path)]
            if password:
                cmd.insert(3, f"-p{password}")
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode == 0:
                return _promote_tree(temp_dir, output_dir)
            shutil.rmtree(temp_dir)
            temp_dir.mkdir()
        raise RuntimeError(result.stderr.strip() or f"failed to extract {archive_path.name}")


def extract_archive(archive_path: Path, *, passwords: list[str] | None = None) -> int:
    passwords = passwords or []
    output_dir = _output_dir_for(archive_path)
    if output_dir.exists() and any(output_dir.rglob("*")):
        logger.debug(f"[ARCHIVE] Skipping {archive_path.name}; output already exists at {output_dir}")
        return 0

    lower_name = archive_path.name.lower()
    if lower_name.endswith(".zip"):
        return _extract_zip(archive_path, output_dir, passwords)
    if lower_name.endswith((".tar", ".tar.gz", ".tar.bz2", ".tar.xz", ".tgz", ".tbz2", ".tbz", ".txz")):
        return _extract_tar(archive_path, output_dir)
    if archive_path.suffix.lower() in _COMPRESSED_SUFFIXES:
        return _extract_single_file(archive_path, output_dir)
    if lower_name.endswith((".7z", ".rar")):
        return _extract_with_7z(archive_path, output_dir, passwords)
    raise RuntimeError(f"unsupported archive type: {archive_path.name}")


def extract_archives(root: str | Path, *, passwords: list[str] | None = None) -> dict[str, int]:
    root_path = Path(root)
    results: dict[str, int] = {}
    for archive_path in _iter_archives(root_path):
        try:
            results[str(archive_path)] = extract_archive(archive_path, passwords=passwords)
        except Exception as exc:
            logger.warning(f"[ARCHIVE] Failed to extract {archive_path.name}: {exc}")
            results[str(archive_path)] = -1
    return results
