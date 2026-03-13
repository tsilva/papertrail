"""Compatibility wrapper for the canonical pipeline workflow."""

from papertrail.app import get_app
from papertrail.workflows import pipeline as workflow_pipeline

__all__ = ["pipeline"]


def pipeline(months=2, export_date_arg=None, processed_path_override=None):
    return workflow_pipeline(
        get_app(),
        months=months,
        export_date_arg=export_date_arg,
        processed_path_override=processed_path_override,
    )
