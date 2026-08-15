from __future__ import annotations

import io
import tomllib
import zlib
from importlib.metadata import version
from pathlib import Path

import pytest
from httplib2.decode import DecodeRatioError, LimitDecoder, ZlibDecoder
from PIL import Image, UnidentifiedImageError
from pyasn1.codec.ber.decoder import decode as decode_ber
from pyasn1.error import PyAsn1Error

ROOT = Path(__file__).resolve().parents[1]


def test_patched_dependency_versions_are_installed() -> None:
    assert tuple(map(int, version("pillow").split("."))) >= (12, 3, 0)
    assert tuple(map(int, version("httplib2").split("."))) >= (0, 32, 0)
    assert tuple(map(int, version("pyasn1").split("."))) >= (0, 6, 4)


def test_pillow_accepts_valid_image_and_rejects_malformed_input() -> None:
    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color=(12, 34, 56)).save(buffer, format="PNG")

    with Image.open(io.BytesIO(buffer.getvalue())) as image:
        image.load()
        assert image.size == (2, 2)

    with pytest.raises((UnidentifiedImageError, OSError)):
        with Image.open(io.BytesIO(b"not an image")) as image:
            image.load()


def test_pyasn1_rejects_excessively_long_ber_tag_identifier() -> None:
    malicious_ber = b"\x1f" + (b"\x81" * 21) + b"\x01\x00"

    with pytest.raises(PyAsn1Error):
        decode_ber(malicious_ber)


def test_httplib2_limits_decompression_amplification() -> None:
    plaintext = b"A" * 4_096
    compressed = zlib.compress(plaintext)

    with pytest.raises(DecodeRatioError):
        LimitDecoder(ZlibDecoder(), ratio=10, safe_limit=1).consume_bytes(compressed)

    assert (
        LimitDecoder(ZlibDecoder(), ratio=1_000, safe_limit=1).consume_bytes(compressed)
        == plaintext
    )


def test_dependency_manifests_use_registry_sources_only() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        manifest = tomllib.load(file)
    with (ROOT / "uv.lock").open("rb") as file:
        lock = tomllib.load(file)

    declared = list(manifest["project"]["dependencies"])
    for dependencies in manifest["project"].get("optional-dependencies", {}).values():
        declared.extend(dependencies)
    assert not any(
        dependency.lower().startswith(("file:", "git:", "git+", "http:", "https:"))
        for dependency in declared
    )

    for package in lock["package"]:
        source = package.get("source", {})
        if source.get("editable") == ".":
            continue
        assert source == {"registry": "https://pypi.org/simple"}
