"""The sole model transport is AgentBridge."""

from __future__ import annotations

import os
from urllib.parse import urlparse

DEFAULT_BASE_URL = "http://127.0.0.1:8082/api/v1"


def base_url(value: str | None = None) -> str:
    value = (os.getenv("AGENTBRIDGE_BASE_URL") or value or DEFAULT_BASE_URL).rstrip("/")
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.hostname == "openrouter.ai"
    ):
        raise ValueError("Configure an AgentBridge HTTP endpoint")
    return value


def model_id(value: str) -> str:
    if (
        value.startswith(("codex/", "claudecode/"))
        or (value.startswith("openrouter/") and value.count("/") >= 2)
        or value == "openrouter/default"
    ):
        return value
    return f"openrouter/{value}"
