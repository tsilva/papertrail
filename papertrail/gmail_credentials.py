"""OAuth storage, including refresh writeback, without exposing secret values."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from papertrail.config import ConfigError, GmailInfisicalSettings, GmailSettings


def _json_object(value: str, label: str) -> dict:
    try:
        data = json.loads(value)
    except (ValueError, TypeError):
        raise ConfigError(f"{label} must contain a JSON object.") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{label} must contain a JSON object.")
    return data


class FileGmailCredentialStore:
    def __init__(self, client_path: Path, token_path: Path) -> None:
        self.client_path = client_path
        self.token_path = token_path

    def load_token(self) -> dict | None:
        if not self.token_path.exists():
            return None
        return _json_object(self.token_path.read_text(encoding="utf-8"), "Gmail token")

    def load_client(self) -> dict:
        if not self.client_path.exists():
            raise FileNotFoundError(
                f"Gmail credentials not found at {self.client_path}. "
                "Download from Google Cloud Console and save to this path."
            )
        return _json_object(self.client_path.read_text(encoding="utf-8"), "Gmail client config")

    def save_token(self, value: str) -> None:
        _json_object(value, "Gmail token")
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".gmail-token-", dir=self.token_path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(value)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.token_path)
        finally:
            Path(temporary).unlink(missing_ok=True)


class InfisicalGmailCredentialStore:
    def __init__(self, settings: GmailInfisicalSettings) -> None:
        if not settings.project_id or not settings.project_id.strip():
            raise ConfigError("gmail.infisical.project_id is required for the Infisical backend.")
        self.settings = settings

    def _run(self, operation: str, secret_name: str, *, value: str | None = None) -> str:
        args = [self.settings.executable, "secrets", operation]
        if operation == "get":
            args.extend([secret_name, "--plain", "--expand=false", "--include-imports=false"])
        else:
            # The JSON is sent over a pipe; no secret in argv, shell, or a temporary file.
            args.append(f"{secret_name}=@/dev/stdin")
        args.extend(
            [
                "--projectId",
                self.settings.project_id,
                "--env",
                self.settings.environment,
                "--path",
                self.settings.path,
                "--silent",
                "--telemetry=false",
            ]
        )
        if self.settings.login_profile:
            args.extend(["--profile", self.settings.login_profile])
        try:
            result = subprocess.run(
                args,
                **({"input": value} if value is not None else {"stdin": subprocess.DEVNULL}),
                text=True,
                capture_output=True,
                timeout=self.settings.timeout_seconds,
                check=False,
            )
        except FileNotFoundError:
            raise ConfigError(
                "Infisical CLI is unavailable; install it or configure its executable."
            ) from None
        except (OSError, subprocess.TimeoutExpired):
            raise ConfigError(
                f"Infisical Gmail credential {operation} could not complete."
            ) from None
        if result.returncode:
            # CLI errors can contain sensitive material; never forward their output.
            raise ConfigError(
                f"Infisical Gmail credential {operation} failed. Check your CLI login, "
                "project/environment/path, secret names, and read/write permissions."
            )
        return result.stdout

    def load_token(self) -> dict:
        return _json_object(
            self._run("get", self.settings.token_secret_name), "Infisical Gmail token"
        )

    def load_client(self) -> dict:
        return _json_object(
            self._run("get", self.settings.client_secret_name), "Infisical Gmail client config"
        )

    def save_token(self, value: str) -> None:
        _json_object(value, "Gmail token")
        self._run("set", self.settings.token_secret_name, value=value)


def gmail_credential_store(
    settings: GmailSettings, client_path: Path, token_path: Path
) -> FileGmailCredentialStore | InfisicalGmailCredentialStore:
    if settings.credential_backend == "infisical":
        return InfisicalGmailCredentialStore(settings.infisical)
    return FileGmailCredentialStore(client_path, token_path)
