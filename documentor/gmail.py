"""Gmail API integration for downloading email attachments."""

import base64
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tqdm import tqdm

from documentor.config import get_gmail_config_paths

# Gmail API scope - read-only access to messages
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Default settings if config file doesn't exist
DEFAULT_SETTINGS = {
    "attachment_mime_types": ["application/pdf"],
    "label_filter": None,
    "max_results_per_query": 500,
    "skip_already_downloaded": True,
}


def setup_failure_logger(log_path: Path) -> logging.Logger:
    """Set up a logger for download failures."""
    logger = logging.getLogger("gmail_download_failures")
    logger.setLevel(logging.ERROR)

    # Remove existing handlers
    logger.handlers.clear()

    handler = logging.FileHandler(log_path, mode="a")
    handler.setFormatter(logging.Formatter("%(asctime)s - %(message)s"))
    logger.addHandler(handler)

    return logger


class GmailDownloader:
    """Download email attachments from Gmail."""

    def __init__(
        self,
        credentials_path: Path,
        token_path: Path,
        settings_path: Path,
        output_dir: Path,
    ):
        """
        Initialize Gmail downloader.

        Args:
            credentials_path: Path to OAuth2 client credentials JSON
            token_path: Path to store/load refresh token
            settings_path: Path to Gmail settings JSON
            output_dir: Directory to save downloaded attachments
        """
        self.credentials_path = Path(credentials_path)
        self.token_path = Path(token_path)
        self.settings_path = Path(settings_path)
        self.output_dir = Path(output_dir)
        self.settings = self._load_settings()
        self.service = None
        self.failure_logger = None

    def _load_settings(self) -> dict:
        """Load Gmail settings from config file."""
        if self.settings_path.exists():
            with open(self.settings_path, "r") as f:
                return json.load(f)
        return DEFAULT_SETTINGS.copy()

    def authenticate(self) -> bool:
        """
        Authenticate with Gmail API using OAuth2.

        On first run, opens browser for user authorization.
        On subsequent runs, uses stored refresh token.

        Returns:
            True if authentication successful

        Raises:
            FileNotFoundError: If credentials file doesn't exist
        """
        creds = None

        # Load existing token if available
        if self.token_path.exists():
            creds = Credentials.from_authorized_user_file(str(self.token_path), SCOPES)

        # If no valid credentials, authenticate
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                # Refresh expired token
                creds.refresh(Request())
            else:
                # Interactive OAuth2 flow
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

            # Save token for future use
            with open(self.token_path, "w") as token_file:
                token_file.write(creds.to_json())

        self.service = build("gmail", "v1", credentials=creds)
        return True

    def build_search_query(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> str:
        """
        Build Gmail search query for date range with attachments.

        Args:
            start_date: Start of date range (inclusive)
            end_date: End of date range (inclusive)

        Returns:
            Gmail search query string
        """
        # Gmail date format: YYYY/MM/DD
        # Use after: for start (inclusive) and before: for end (day after for inclusive)
        start_str = start_date.strftime("%Y/%m/%d")
        end_plus_one = end_date + timedelta(days=1)
        end_str = end_plus_one.strftime("%Y/%m/%d")

        query = f"has:attachment after:{start_str} before:{end_str}"

        if self.settings.get("label_filter"):
            query += f" label:{self.settings['label_filter']}"

        return query

    def list_messages(self, query: str) -> list[dict]:
        """
        List messages matching the search query.

        Args:
            query: Gmail search query

        Returns:
            List of message metadata dicts with 'id' field
        """
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

    def _extract_attachments_from_parts(
        self, parts: list[dict], allowed_types: set[str]
    ) -> list[dict]:
        """Recursively extract attachments from message parts."""
        attachments = []

        for part in parts:
            mime_type = part.get("mimeType", "")
            filename = part.get("filename", "")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")

            # Check for nested parts (multipart messages)
            if "parts" in part:
                attachments.extend(
                    self._extract_attachments_from_parts(part["parts"], allowed_types)
                )

            # Check if this part is a matching attachment
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
        """
        Extract attachment metadata from a message.

        Returns list of dicts with:
        - filename: Original filename
        - mime_type: MIME type
        - attachment_id: Gmail attachment ID
        - size: Attachment size in bytes
        """
        allowed_types = set(
            self.settings.get("attachment_mime_types", ["application/pdf"])
        )
        payload = message.get("payload", {})
        parts = payload.get("parts", [])

        return self._extract_attachments_from_parts(parts, allowed_types)

    def download_attachment(
        self,
        message_id: str,
        attachment_id: str,
        filename: str,
    ) -> Optional[Path]:
        """
        Download a single attachment and save to output directory.

        Args:
            message_id: Gmail message ID
            attachment_id: Gmail attachment ID
            filename: Original filename

        Returns:
            Path to saved file, or None if failed
        """
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

            # Generate unique filename to avoid collisions
            output_path = self._generate_unique_path(filename)

            with open(output_path, "wb") as f:
                f.write(file_data)

            return output_path

        except HttpError as e:
            if self.failure_logger:
                self.failure_logger.error(f"Failed to download {filename}: {e}")
            return None

    def _generate_unique_path(self, filename: str) -> Path:
        """Generate unique output path, handling filename collisions."""
        base_path = self.output_dir / filename

        if not base_path.exists():
            return base_path

        # Add timestamp to make unique
        stem = base_path.stem
        suffix = base_path.suffix
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")

        return self.output_dir / f"{stem}_{timestamp}{suffix}"

    def _get_processed_messages_path(self) -> Path:
        """Get path to processed messages tracking file."""
        return self.output_dir / "gmail_processed_messages.json"

    def load_processed_messages(self) -> set[str]:
        """Load set of already-processed message IDs."""
        processed_file = self._get_processed_messages_path()

        if processed_file.exists():
            with open(processed_file, "r") as f:
                return set(json.load(f))
        return set()

    def save_processed_messages(self, message_ids: set[str]) -> None:
        """Save set of processed message IDs."""
        processed_file = self._get_processed_messages_path()

        with open(processed_file, "w") as f:
            json.dump(sorted(message_ids), f, indent=2)

    def download_attachments_in_range(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> dict:
        """
        Download all attachments from emails in date range.

        Args:
            start_date: Start of date range
            end_date: End of date range

        Returns:
            Stats dict with counts
        """
        stats = {
            "messages_found": 0,
            "messages_processed": 0,
            "messages_skipped": 0,
            "attachments_downloaded": 0,
            "attachments_failed": 0,
            "bytes_downloaded": 0,
        }

        # Setup failure logging
        log_path = self.output_dir / "gmail_download_failures.log"
        self.failure_logger = setup_failure_logger(log_path)

        # Build query and list messages
        query = self.build_search_query(start_date, end_date)
        print(f"Gmail search query: {query}")

        messages = self.list_messages(query)
        stats["messages_found"] = len(messages)
        print(f"Found {len(messages)} messages with attachments")

        if not messages:
            return stats

        # Load already processed messages
        processed_ids = set()
        if self.settings.get("skip_already_downloaded", True):
            processed_ids = self.load_processed_messages()
            if processed_ids:
                print(f"Already processed: {len(processed_ids)} messages")

        # Process messages
        for msg_meta in tqdm(messages, desc="Downloading attachments"):
            msg_id = msg_meta["id"]

            if msg_id in processed_ids:
                stats["messages_skipped"] += 1
                continue

            try:
                message = self.get_message(msg_id)
                attachments = self.extract_attachments(message)

                for att in attachments:
                    output_path = self.download_attachment(
                        msg_id, att["attachment_id"], att["filename"]
                    )

                    if output_path:
                        stats["attachments_downloaded"] += 1
                        stats["bytes_downloaded"] += att["size"]
                    else:
                        stats["attachments_failed"] += 1

                processed_ids.add(msg_id)
                stats["messages_processed"] += 1

            except HttpError as e:
                if self.failure_logger:
                    self.failure_logger.error(f"Failed to process message {msg_id}: {e}")
                stats["attachments_failed"] += 1

        # Save processed message IDs
        self.save_processed_messages(processed_ids)

        return stats


def download_gmail_attachments(
    output_dir: Path,
    start_date: datetime,
    end_date: datetime,
) -> dict:
    """
    Download Gmail attachments for the specified date range.

    Args:
        output_dir: Directory to save downloaded files
        start_date: Start of date range
        end_date: End of date range

    Returns:
        Download statistics dict
    """
    paths = get_gmail_config_paths()

    downloader = GmailDownloader(
        credentials_path=paths["credentials"],
        token_path=paths["token"],
        settings_path=paths["settings"],
        output_dir=output_dir,
    )

    print("Authenticating with Gmail API...")
    downloader.authenticate()
    print("Authentication successful!")

    return downloader.download_attachments_in_range(start_date, end_date)
