"""Public papertrail interfaces."""

from papertrail.app import App, AppPaths, create_app
from papertrail.rules import RuleEngine
from papertrail.services import DocumentService, UpsertResult, upsert_document
from papertrail.store import DocumentStore

__all__ = [
    "App",
    "AppPaths",
    "DocumentService",
    "DocumentStore",
    "RuleEngine",
    "UpsertResult",
    "create_app",
    "upsert_document",
]
"""papertrail - AI-powered PDF document classification and organization."""
