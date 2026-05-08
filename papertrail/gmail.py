"""Gmail API integration for downloading email attachments."""

from __future__ import annotations

import base64
import json
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from papertrail.config import GmailSettings, get_gmail_config_paths
from papertrail.console import PapertrailConsole
from papertrail.logging_utils import get_logger, setup_failure_logger

logger = get_logger("gmail")

def _default_settings() -> dict:
    return GmailSettings().model_dump()


def _slugify(text: str, *, max_chars: int = 80) -> str:
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    text = re.sub(r"-{2,}", "-", text)
    return text[:max_chars] or "no-subject"


def _settings_to_dict(settings: GmailSettings | dict | None) -> dict:
    if settings is None:
        return _default_settings()
    if isinstance(settings, GmailSettings):
        data = settings.model_dump()
    else:
        data = dict(settings)
    merged = _default_settings()
    merged.update({key: value for key, value in data.items() if value is not None})
    return merged


class GmailDownloader:
    """Download email attachments from Gmail."""

    def __init__(
        self,
        credentials_path: Path,
        token_path: Path,
        settings_path: Path,
        output_dir: Path,
        *,
        tracking_dir: Optional[Path] = None,
        settings: GmailSettings | dict | None = None,
        console: PapertrailConsole | None = None,
    ) -> None:
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.settings_path = Path(settings_path)
        self.output_dir = Path(output_dir)
        self.tracking_dir = Path(tracking_dir) if tracking_dir else self.output_dir
        self.settings = self._load_settings(settings)
        self.console = console
        self.service = None
        self.failure_logger = None

    def _load_settings(self, settings: GmailSettings | dict | None) -> dict:
        if settings is not None:
            return _settings_to_dict(settings)
        if self.settings_path.exists():
            with open(self.settings_path, "r", encoding="utf-8") as handle:
                return _settings_to_dict(json.load(handle))
        return _default_settings()

    def authenticate(self) -> bool:
        creds = None
        scopes = list(self.settings.get("scopes") or GmailSettings().scopes)
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), scopes)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Gmail credentials not found at {self.credentials_path}. "
                        "Download from Google Cloud Console and save to this path."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.credentials_path), scopes)
                creds = flow.run_local_server(port=0)

            with open(self.token_path, "w", encoding="utf-8") as token_file:
                token_file.write(creds.to_json())

        self.service = build(
            str(self.settings.get("api_service") or "gmail"),
            str(self.settings.get("api_version") or "v1"),
            credentials=creds,
        )
        return True

    def build_search_query(self, start_date: datetime, end_date: datetime) -> str:
        start_str = start_date.strftime("%Y/%m/%d")
        end_str = (end_date + timedelta(days=1)).strftime("%Y/%m/%d")
        query = f"has:attachment after:{start_str} before:{end_str}"
        if self.settings.get("label_filter"):
            query += f" label:{self.settings['label_filter']}"
        return query

    def list_messages(self, query: str) -> list[dict]:
        messages = []
        page_token = None
        max_results = self.settings.get("max_results_per_query", 500)
        api_page_size = int(self.settings.get("api_page_size") or 100)

        while True:
            result = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    pageToken=page_token,
                    maxResults=min(api_page_size, max_results - len(messages)),
                )
                .execute()
            )
            if "messages" in result:
                messages.extend(result["messages"])
            page_token = result.get("nextPageToken")
            if not page_token or len(messages) >= max_results:
                break
        return messages

    def get_message(self, message_id: str) -> dict:
        return self.service.users().messages().get(userId="me", id=message_id).execute()

    def _extract_attachments_from_parts(self, parts: list[dict], allowed_types: set[str]) -> list[dict]:
        attachments = []
        for part in parts:
            mime_type = part.get("mimeType", "")
            filename = part.get("filename", "")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")

            if "parts" in part:
                attachments.extend(self._extract_attachments_from_parts(part["parts"], allowed_types))

            if attachment_id and filename and mime_type not in allowed_types:
                generic_mime_types = set(self.settings.get("generic_mime_types") or [])
                if mime_type in generic_mime_types:
                    ext = Path(filename).suffix.lower()
                    resolved_mime = dict(self.settings.get("extension_mime_types") or {}).get(ext)
                    if resolved_mime and resolved_mime in allowed_types:
                        mime_type = resolved_mime
                        logger.info(f"[GMAIL] Accepted '{filename}' by extension ({ext} -> {resolved_mime})")
                    else:
                        logger.info(
                            f"[GMAIL] Skipped '{filename}' (generic mime={mime_type}, ext={ext} not in allowed types)"
                        )
                else:
                    logger.info(f"[GMAIL] Skipped '{filename}' (mime_type={mime_type}, not in allowed types)")

            if attachment_id and mime_type in allowed_types and filename:
                attachments.append(
                    {
                        "filename": filename,
                        "mime_type": mime_type,
                        "attachment_id": attachment_id,
                        "size": body.get("size", 0),
                    }
                )
        return attachments

    def extract_attachments(self, message: dict) -> list[dict]:
        allowed_types = set(self.settings.get("attachment_mime_types", ["application/pdf"]))
        payload = message.get("payload", {})
        parts = payload.get("parts", [])
        if not parts and payload.get("body", {}).get("attachmentId"):
            parts = [payload]
            logger.info("[GMAIL] Single-part email detected, checking payload directly")
        return self._extract_attachments_from_parts(parts, allowed_types)

    def _get_message_dir(self, message: dict) -> Path:
        headers = {header["name"].lower(): header["value"] for header in message.get("payload", {}).get("headers", [])}
        internal_date = message.get("internalDate", "")
        if internal_date:
            dt = datetime.fromtimestamp(int(internal_date) / 1000)
            date_str = dt.strftime("%Y-%m-%d")
        else:
            date_str = "unknown-date"
        subject = headers.get("subject", "")
        max_chars = int(self.settings.get("subject_slug_max_chars") or 80)
        subject_slug = _slugify(subject, max_chars=max_chars) if subject else "no-subject"
        return self.output_dir / f"{date_str} - {subject_slug}"

    def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        filename: str,
        *,
        output_dir: Optional[Path] = None,
    ) -> tuple[Optional[Path], bool]:
        base_dir = output_dir or self.output_dir
        base_dir.mkdir(parents=True, exist_ok=True)
        target_path = base_dir / filename

        # If the file is already present, reuse it instead of creating a duplicate.
        if self.settings.get("skip_already_downloaded", True) and target_path.exists():
            return target_path, True

        try:
            result = (
                self.service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
            data = result.get("data", "")
            file_data = base64.urlsafe_b64decode(data)
            output_path = self._generate_unique_path(filename, base_dir=base_dir)
            with open(output_path, "wb") as handle:
                handle.write(file_data)
            return output_path, False
        except HttpError as exc:
            if self.failure_logger:
                self.failure_logger.error(f"Failed to download {filename}: {exc}")
            return None, False

    def _generate_unique_path(self, filename: str, *, base_dir: Optional[Path] = None) -> Path:
        directory = base_dir or self.output_dir
        directory.mkdir(parents=True, exist_ok=True)
        base_path = directory / filename
        if not base_path.exists():
            return base_path
        stem = base_path.stem
        suffix = base_path.suffix
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        return directory / f"{stem}_{timestamp}{suffix}"

    def _get_processed_messages_path(self) -> Path:
        return self.tracking_dir / "gmail_processed_messages.json"

    def load_processed_messages(self) -> set[str]:
        processed_file = self._get_processed_messages_path()
        if processed_file.exists():
            with open(processed_file, "r", encoding="utf-8") as handle:
                return set(json.load(handle))
        return set()

    def save_processed_messages(self, message_ids: set[str]) -> None:
        processed_file = self._get_processed_messages_path()
        processed_file.parent.mkdir(parents=True, exist_ok=True)
        with open(processed_file, "w", encoding="utf-8") as handle:
            json.dump(sorted(message_ids), handle, indent=2)

    def download_attachments_in_range(
        self,
        start_date: datetime,
        end_date: datetime,
        *,
        quiet: bool = False,
    ) -> dict:
        stats = {
            "messages_found": 0,
            "messages_processed": 0,
            "messages_skipped": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "bytes_downloaded": 0,
        }

        log_path = self.tracking_dir / "gmail_download_failures.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.failure_logger = setup_failure_logger(log_path)

        query = self.build_search_query(start_date, end_date)
        logger.debug(f"Gmail search query: {query}")
        messages = self.list_messages(query)
        stats["messages_found"] = len(messages)
        logger.info(f"Found {len(messages)} messages with attachments")
        if not messages:
            return stats

        processed_ids = set()
        if self.settings.get("skip_already_downloaded", True):
            processed_ids = self.load_processed_messages()
            if processed_ids:
                logger.info(f"Already processed: {len(processed_ids)} messages")

        iterator = self.console.track(messages, "Downloading attachments") if self.console and not quiet else messages
        for msg_meta in iterator:
            msg_id = msg_meta["id"]
            if msg_id in processed_ids:
                stats["messages_skipped"] += 1
                continue

            try:
                message = self.get_message(msg_id)
                msg_dir = self._get_message_dir(message)
                attachments = self.extract_attachments(message)

                downloaded_count = 0
                for attachment in attachments:
                    output_path, already_present = self.download_attachment(
                        msg_id,
                        attachment["attachment_id"],
                        attachment["filename"],
                        output_dir=msg_dir,
                    )
                    if output_path:
                        downloaded_count += 1
                        if not already_present:
                            stats["attachments_downloaded"] += 1
                            stats["bytes_downloaded"] += attachment["size"]
                    else:
                        stats["attachments_failed"] += 1

                if downloaded_count > 0:
                    processed_ids.add(msg_id)
                    stats["messages_processed"] += 1
            except HttpError as exc:
                if self.failure_logger:
                    self.failure_logger.error(f"Failed to process message {msg_id}: {exc}")
                stats["attachments_failed"] += 1

        self.save_processed_messages(processed_ids)
        return stats


def download_gmail_attachments(
    output_dir: Path,
    start_date: datetime,
    end_date: datetime,
    *,
    quiet: bool = False,
    tracking_dir: Optional[Path] = None,
    credentials_path: Path | None = None,
    token_path: Path | None = None,
    settings_path: Path | None = None,
    settings: GmailSettings | dict | None = None,
    console: PapertrailConsole | None = None,
) -> dict:
    if credentials_path is None or token_path is None or settings_path is None:
        paths = get_gmail_config_paths()
        credentials_path = credentials_path or paths["credentials"]
        token_path = token_path or paths["token"]
        settings_path = settings_path or paths["settings"]

    downloader = GmailDownloader(
        credentials_path=credentials_path,
        token_path=token_path,
        settings_path=settings_path,
        output_dir=output_dir,
        tracking_dir=tracking_dir,
        settings=settings,
        console=console,
    )

    logger.info("Authenticating with Gmail API...")
    downloader.authenticate()
    logger.info("Authentication successful!")
    return downloader.download_attachments_in_range(start_date, end_date, quiet=quiet)
