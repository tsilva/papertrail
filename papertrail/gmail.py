"""Gmail API integration for downloading email attachments."""

import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from papertrail.config import get_gmail_config_paths, get_current_profile
from papertrail.console import get_console
from papertrail.logging_utils import setup_failure_logger, get_logger

logger = get_logger('gmail')

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_DEFAULT_SETTINGS = {
    "attachment_mime_types": ["application/pdf"],
    "label_filter": None,
    "max_results_per_query": 500,
    "skip_already_downloaded": True,
}


class GmailDownloader:
    """Download email attachments from Gmail."""

    def __init__(
        self,
        credentials_path: Path,
        token_path: Path,
        settings_path: Path,
        output_dir: Path,
        tracking_dir: Optional[Path] = None,
    ):
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.settings_path = Path(settings_path)
        self.output_dir = Path(output_dir)
        self.tracking_dir = Path(tracking_dir) if tracking_dir else self.output_dir
        self.settings = self._load_settings()
        self.service = None
        self.failure_logger = None

    def _load_settings(self) -> dict:
        profile = get_current_profile()
        if profile and profile.gmail.enabled:
            return {
                "attachment_mime_types": profile.gmail.attachment_mime_types,
                "label_filter": profile.gmail.label_filter,
                "max_results_per_query": profile.gmail.max_results_per_query,
                "skip_already_downloaded": profile.gmail.skip_already_downloaded,
            }

        if self.settings_path.exists():
            with open(self.settings_path, "r") as f:
                return json.load(f)

        return _DEFAULT_SETTINGS.copy()

    def authenticate(self) -> bool:
        """Authenticate with Gmail API using OAuth2."""
        creds = None
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not self.credentials_path.exists():
                    raise FileNotFoundError(
                        f"Gmail credentials not found at {self.credentials_path}. "
                        "Download from Google Cloud Console and save to this path. "
                        "See config/examples/gmail_credentials.json.example for instructions."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(self.credentials_path), SCOPES
                )
                creds = flow.run_local_server(port=0)

            with open(self.token_path, "w") as token_file:
                token_file.write(creds.to_json())

        self.service = build("gmail", "v1", credentials=creds)
        return True

    def build_search_query(self, start_date: datetime, end_date: datetime) -> str:
        """Build Gmail search query for date range with attachments."""
        start_str = start_date.strftime("%Y/%m/%d")
        end_plus_one = end_date + timedelta(days=1)
        end_str = end_plus_one.strftime("%Y/%m/%d")

        query = f"has:attachment after:{start_str} before:{end_str}"

        if self.settings.get("label_filter"):
            query += f" label:{self.settings['label_filter']}"

        return query

    def list_messages(self, query: str) -> list[dict]:
        """List messages matching the search query."""
        messages = []
        page_token = None
        max_results = self.settings.get("max_results_per_query", 500)

        while True:
            result = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    pageToken=page_token,
                    maxResults=min(100, max_results - len(messages)),
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
        """Fetch full message content."""
        return (
            self.service.users()
            .messages()
            .get(userId="me", id=message_id)
            .execute()
        )

    def _extract_attachments_from_parts(self, parts: list[dict], allowed_types: set[str]) -> list[dict]:
        attachments = []

        for part in parts:
            mime_type = part.get("mimeType", "")
            filename = part.get("filename", "")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")

            if "parts" in part:
                attachments.extend(
                    self._extract_attachments_from_parts(part["parts"], allowed_types)
                )

            if attachment_id and filename and mime_type not in allowed_types:
                logger.debug(f"Skipped attachment '{filename}' (mime_type={mime_type}, not in {allowed_types})")

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
        """Extract attachment metadata from a message."""
        allowed_types = set(
            self.settings.get("attachment_mime_types", ["application/pdf"])
        )
        payload = message.get("payload", {})
        parts = payload.get("parts", [])

        return self._extract_attachments_from_parts(parts, allowed_types)

    def download_attachment(self, message_id: str, attachment_id: str, filename: str) -> Optional[Path]:
        """Download a single attachment. Returns path or None on failure."""
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

            output_path = self._generate_unique_path(filename)

            with open(output_path, "wb") as f:
                f.write(file_data)

            return output_path

        except HttpError as e:
            if self.failure_logger:
                self.failure_logger.error(f"Failed to download {filename}: {e}")
            return None

    def _generate_unique_path(self, filename: str) -> Path:
        base_path = self.output_dir / filename

        if not base_path.exists():
            return base_path

        stem = base_path.stem
        suffix = base_path.suffix
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        return self.output_dir / f"{stem}_{timestamp}{suffix}"

    def _get_processed_messages_path(self) -> Path:
        return self.tracking_dir / "gmail_processed_messages.json"

    def load_processed_messages(self) -> set[str]:
        processed_file = self._get_processed_messages_path()

        if processed_file.exists():
            with open(processed_file, "r") as f:
                return set(json.load(f))
        return set()

    def save_processed_messages(self, message_ids: set[str]) -> None:
        processed_file = self._get_processed_messages_path()

        with open(processed_file, "w") as f:
            json.dump(sorted(message_ids), f, indent=2)

    def download_attachments_in_range(self, start_date: datetime, end_date: datetime, quiet: bool = False) -> dict:
        """Download all attachments from emails in date range."""
        stats = {
            "messages_found": 0,
            "messages_processed": 0,
            "messages_skipped": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "bytes_downloaded": 0,
        }

        log_path = self.tracking_dir / "gmail_download_failures.log"
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

        iterator = get_console().track(messages, "Downloading attachments") if not quiet else messages
        for msg_meta in iterator:
            msg_id = msg_meta["id"]

            if msg_id in processed_ids:
                stats["messages_skipped"] += 1
                continue

            try:
                message = self.get_message(msg_id)
                attachments = self.extract_attachments(message)

                downloaded_count = 0
                for att in attachments:
                    output_path = self.download_attachment(
                        msg_id, att["attachment_id"], att["filename"]
                    )

                    if output_path:
                        downloaded_count += 1
                        stats["attachments_downloaded"] += 1
                        stats["bytes_downloaded"] += att["size"]
                    else:
                        stats["attachments_failed"] += 1

                if downloaded_count > 0:
                    processed_ids.add(msg_id)
                    stats["messages_processed"] += 1

            except HttpError as e:
                if self.failure_logger:
                    self.failure_logger.error(f"Failed to process message {msg_id}: {e}")
                stats["attachments_failed"] += 1

        self.save_processed_messages(processed_ids)

        return stats


def download_gmail_attachments(
    output_dir: Path,
    start_date: datetime,
    end_date: datetime,
    quiet: bool = False,
    tracking_dir: Optional[Path] = None,
) -> dict:
    """Download Gmail attachments for the specified date range."""
    paths = get_gmail_config_paths()

    downloader = GmailDownloader(
        credentials_path=paths["credentials"],
        token_path=paths["token"],
        settings_path=paths["settings"],
        output_dir=output_dir,
        tracking_dir=tracking_dir,
    )

    logger.info("Authenticating with Gmail API...")
    downloader.authenticate()
    logger.info("Authentication successful!")

    return downloader.download_attachments_in_range(start_date, end_date, quiet=quiet)
