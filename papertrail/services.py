"""Public service interfaces."""

from papertrail.tasks.extraction import DocumentService, UpsertResult, upsert_document

__all__ = ["DocumentService", "UpsertResult", "upsert_document"]
