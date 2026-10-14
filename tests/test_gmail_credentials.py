import json
import stat
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError

from papertrail.config import ConfigError, GmailInfisicalSettings
from papertrail.gmail import GmailDownloader
from papertrail.gmail_credentials import FileGmailCredentialStore, InfisicalGmailCredentialStore


def test_infisical_refresh_writes_rotated_token_without_local_files(tmp_path):
    token = {"refresh_token": "old-test-token"}
    rotated = json.dumps({"refresh_token": "rotated-test-token", "token": "new-access"})
    credentials = MagicMock(valid=False, expired=True, refresh_token="old-test-token")
    credentials.to_json.return_value = rotated
    downloader = GmailDownloader(
        tmp_path / "client.json",
        tmp_path / "token.json",
        tmp_path / "output",
        settings={"credential_backend": "infisical", "infisical": {"project_id": "test-project"}},
    )
    responses = [
        subprocess.CompletedProcess([], 0, json.dumps(token), ""),
        subprocess.CompletedProcess([], 0, "stored", ""),
    ]
    with (
        patch("papertrail.gmail_credentials.subprocess.run", side_effect=responses) as cli,
        patch("papertrail.gmail.Credentials.from_authorized_user_info", return_value=credentials),
        patch("papertrail.gmail.build", return_value=SimpleNamespace()) as build,
    ):
        assert downloader.authenticate()

    credentials.refresh.assert_called_once()
    build.assert_called_once()
    assert len(cli.call_args_list) == 2
    write = cli.call_args_list[1]
    assert "PAPERTRAIL_GMAIL_TOKEN_JSON=@/dev/stdin" in write.args[0]
    assert write.kwargs["input"] == rotated
    assert all(
        "old-test-token" not in arg and "rotated-test-token" not in arg
        for call in cli.call_args_list
        for arg in call.args[0]
    )
    assert not list(tmp_path.iterdir())


def test_infisical_failure_stops_auth_without_file_fallback_or_secret_error(tmp_path):
    local = tmp_path / "token.json"
    local.write_text('{"refresh_token":"local-token"}')
    downloader = GmailDownloader(
        tmp_path / "client.json",
        local,
        tmp_path / "output",
        settings={"credential_backend": "infisical", "infisical": {"project_id": "test-project"}},
    )
    result = subprocess.CompletedProcess([], 1, "sensitive-value", "sensitive-value")
    with (
        patch("papertrail.gmail_credentials.subprocess.run", return_value=result),
        patch("papertrail.gmail.Credentials.from_authorized_user_info") as loader,
        patch("papertrail.gmail.build") as build,
    ):
        with pytest.raises(ConfigError) as error:
            downloader.authenticate()
    assert "sensitive-value" not in str(error.value)
    loader.assert_not_called()
    build.assert_not_called()
    assert local.read_text() == '{"refresh_token":"local-token"}'


def test_failed_writeback_prevents_successful_authentication(tmp_path):
    credentials = MagicMock(valid=False, expired=True, refresh_token="test-token")
    credentials.to_json.return_value = '{"refresh_token":"test-token"}'
    downloader = GmailDownloader(
        tmp_path / "client.json",
        tmp_path / "token.json",
        tmp_path / "output",
        settings={"credential_backend": "infisical", "infisical": {"project_id": "test-project"}},
    )
    responses = [
        subprocess.CompletedProcess([], 0, '{"refresh_token":"test-token"}', ""),
        subprocess.CompletedProcess([], 1, "", "permission denied"),
    ]
    with (
        patch("papertrail.gmail_credentials.subprocess.run", side_effect=responses),
        patch("papertrail.gmail.Credentials.from_authorized_user_info", return_value=credentials),
        patch("papertrail.gmail.build") as build,
    ):
        with pytest.raises(ConfigError, match="set failed"):
            downloader.authenticate()
    build.assert_not_called()
    assert not list(tmp_path.iterdir())


def test_file_refresh_remains_supported_and_uses_private_atomic_write(tmp_path):
    token = tmp_path / "token.json"
    store = FileGmailCredentialStore(tmp_path / "client.json", token)
    assert store.load_token() is None
    store.save_token('{"refresh_token":"initial"}')
    store.save_token('{"refresh_token":"refreshed"}')
    assert store.load_token() == {"refresh_token": "refreshed"}
    assert stat.S_IMODE(token.stat().st_mode) == 0o600
    assert list(tmp_path.iterdir()) == [token]


def test_local_oauth_login_saves_authorization_through_selected_store(tmp_path):
    client = tmp_path / "client.json"
    client.write_text('{"installed":{"client_id":"test-client"}}')
    creds = MagicMock()
    creds.to_json.return_value = '{"refresh_token":"new-login"}'
    flow = MagicMock()
    flow.run_local_server.return_value = creds
    downloader = GmailDownloader(client, tmp_path / "token.json", tmp_path / "output")
    with (
        patch("papertrail.gmail.InstalledAppFlow.from_client_config", return_value=flow) as loader,
        patch("papertrail.gmail.build"),
    ):
        assert downloader.authenticate()
    loader.assert_called_once_with(
        {"installed": {"client_id": "test-client"}}, downloader.settings.scopes
    )
    assert json.loads((tmp_path / "token.json").read_text()) == {"refresh_token": "new-login"}


@pytest.mark.parametrize("output", ["secret-not-json", "[]", "null"])
def test_invalid_vault_payload_never_appears_in_error(output):
    store = InfisicalGmailCredentialStore(GmailInfisicalSettings(project_id="test-project"))
    with patch(
        "papertrail.gmail_credentials.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, output, ""),
    ):
        with pytest.raises(ConfigError) as error:
            store.load_token()
    assert output not in str(error.value)


def test_infisical_requires_project_before_any_credential_access():
    with patch("papertrail.gmail_credentials.subprocess.run") as cli:
        with pytest.raises(ConfigError, match="project_id is required"):
            InfisicalGmailCredentialStore(GmailInfisicalSettings())
    cli.assert_not_called()


def test_force_reauthorization_bypasses_old_token_and_saves_to_vault(tmp_path):
    client = {"installed": {"client_id": "test-client"}}
    credentials = MagicMock()
    credentials.to_json.return_value = '{"refresh_token":"fresh-authorization"}'
    flow = MagicMock()
    flow.run_local_server.return_value = credentials
    store = MagicMock()
    store.load_client.return_value = client
    downloader = GmailDownloader(tmp_path / "client", tmp_path / "token", tmp_path / "out")
    with (
        patch("papertrail.gmail.gmail_credential_store", return_value=store),
        patch("papertrail.gmail.InstalledAppFlow.from_client_config", return_value=flow),
        patch("papertrail.gmail.build"),
    ):
        assert downloader.authenticate(reauthorize=True)
    store.load_token.assert_not_called()
    store.save_token.assert_called_once_with('{"refresh_token":"fresh-authorization"}')
    assert not list(tmp_path.iterdir())


def test_invalid_refresh_requires_explicit_reconnect_without_secret_details(tmp_path):
    credentials = MagicMock(valid=False, expired=True, refresh_token="test-token")
    credentials.refresh.side_effect = RefreshError("sensitive-provider-error")
    downloader = GmailDownloader(tmp_path / "client", tmp_path / "token", tmp_path / "out")
    store = MagicMock()
    store.load_token.return_value = {"refresh_token": "test-token"}
    with (
        patch("papertrail.gmail.gmail_credential_store", return_value=store),
        patch("papertrail.gmail.Credentials.from_authorized_user_info", return_value=credentials),
        patch("papertrail.gmail.InstalledAppFlow.from_client_config") as flow,
    ):
        with pytest.raises(ConfigError, match="gmail-auth") as error:
            downloader.authenticate()
    assert "sensitive-provider-error" not in str(error.value)
    store.save_token.assert_not_called()
    flow.assert_not_called()
