"""Logging utilities for failure tracking and application-wide logging."""

import logging
import sys
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


# ------------------- APPLICATION LOGGING -------------------

class CleanFormatter(logging.Formatter):
    """Message-only output for normal CLI use."""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


class VerboseFormatter(logging.Formatter):
    """Timestamped output for debug mode."""

    def __init__(self):
        super().__init__(
            fmt='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )


def setup_logging(verbose: bool = False, log_file: Optional[Path] = None) -> logging.Logger:
    """
    Configure documentor logging system.

    Args:
        verbose: If True, show DEBUG messages with timestamps.
                 If False, show INFO messages only (message-only format).
        log_file: Optional path to a log file for debug output.

    Returns:
        The root 'documentor' logger instance.
    """
    root = logging.getLogger('documentor')
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    # Console handler
    console = logging.StreamHandler(sys.stderr)
    if verbose:
        console.setLevel(logging.DEBUG)
        console.setFormatter(VerboseFormatter())
    else:
        console.setLevel(logging.INFO)
        console.setFormatter(CleanFormatter())
    root.addHandler(console)

    # Optional file handler
    if log_file:
        fh = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s'
        ))
        root.addHandler(fh)

    return root


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger under the documentor namespace.

    Args:
        name: Logger name (will be prefixed with 'documentor.')

    Returns:
        Logger instance for documentor.{name}
    """
    return logging.getLogger(f'documentor.{name}')
