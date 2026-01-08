"""Logging utilities for failure tracking."""

import logging
import traceback
from pathlib import Path
from typing import Optional


def setup_failure_logger(log_path: Optional[Path] = None) -> logging.Logger:
    """
    Setup a logger for classification failures with full traceback.

    Args:
        log_path: Path to the log file. Defaults to ./classification_failures.log

    Returns:
        Configured logger instance
    """
    logger = logging.getLogger("documentor.failures")
    logger.setLevel(logging.ERROR)

    # Clear existing handlers
    logger.handlers.clear()

    if log_path is None:
        log_path = Path.cwd() / "classification_failures.log"

    # File handler with detailed format
    file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
    file_handler.setLevel(logging.ERROR)
    formatter = logging.Formatter(
        '\n' + '='*80 + '\n'
        'TIMESTAMP: %(asctime)s\n'
        'FILE: %(pdf_path)s\n'
        'ERROR TYPE: %(error_type)s\n'
        'ERROR MESSAGE: %(message)s\n'
        'TRACEBACK:\n%(traceback)s\n'
        '='*80
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


def log_failure(logger: Optional[logging.Logger], pdf_path: Path, error: Exception) -> None:
    """
    Log a PDF processing failure with full traceback.

    Args:
        logger: The failure logger (can be None, in which case nothing is logged)
        pdf_path: Path to the PDF file that failed
        error: The exception that occurred
    """
    if logger is None:
        return

    logger.error(
        str(error),
        extra={
            'pdf_path': str(pdf_path),
            'error_type': type(error).__name__,
            'traceback': traceback.format_exc()
        }
    )
