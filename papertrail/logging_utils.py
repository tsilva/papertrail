"""Logging utilities for failure tracking and application-wide logging."""

import logging
import sys
import traceback
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.logging import RichHandler


def setup_failure_logger(log_path: Optional[Path] = None) -> logging.Logger:
    """Setup a logger for classification failures with full traceback."""
    logger = logging.getLogger("papertrail.failures")
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
    """Log a PDF processing failure with full traceback."""
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


def setup_logging(
    verbose: bool = False,
    log_file: Optional[Path] = None,
    use_rich: bool = True,
) -> logging.Logger:
    """Configure papertrail logging system."""
    root = logging.getLogger('papertrail')
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.propagate = False

    if use_rich:
        console_obj = Console(stderr=True)
        console = RichHandler(
            console=console_obj,
            show_time=verbose,
            show_path=False,
            rich_tracebacks=True,
            markup=True,
            log_time_format="[%X]" if verbose else "",
        )
        if verbose:
            console.setLevel(logging.DEBUG)
        else:
            console.setLevel(logging.INFO)
    else:
        console = logging.StreamHandler(sys.stderr)
        if verbose:
            console.setLevel(logging.DEBUG)
            console.setFormatter(VerboseFormatter())
        else:
            console.setLevel(logging.INFO)
            console.setFormatter(CleanFormatter())
    root.addHandler(console)

    if log_file:
        fh = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s:%(lineno)d - %(message)s'
        ))
        root.addHandler(fh)

    return root


@contextmanager
def suppress_stdout():
    """Temporarily redirect stdout/stderr to devnull and suppress warnings."""
    import io
    import warnings

    old_stdout, old_stderr = sys.stdout, sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            yield
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr


@contextmanager
def suppress_console_logging():
    """Temporarily suppress console logging output (StreamHandler and RichHandler)."""
    loggers = [logging.getLogger(), logging.getLogger('papertrail')]
    original_levels = []

    for lgr in loggers:
        for handler in lgr.handlers:
            if not isinstance(handler, logging.FileHandler):
                original_levels.append((handler, handler.level))
                handler.setLevel(logging.CRITICAL + 1)

    try:
        yield
    finally:
        for handler, level in original_levels:
            handler.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    """Get a logger under the papertrail namespace."""
    return logging.getLogger(f'papertrail.{name}')


def setup_task_logging(processed_path: Path, task_name: str, verbose: bool = False) -> Path:
    """Setup file-based logging for a task run."""
    logs_dir = processed_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = logs_dir / f"{task_name}_{timestamp}.log"

    root = logging.getLogger('papertrail')
    fh = logging.FileHandler(log_file_path, mode='w', encoding='utf-8')
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        '%(asctime)s [%(levelname)s] %(name)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    ))
    root.addHandler(fh)

    return log_file_path


class DocumentLogger:
    """Structured per-document logging with markers for agent-reviewable auditing."""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self._logger = logger or get_logger('cli')
        self._current_doc: Optional[str] = None
        self._timings: dict[str, float] = {}

    def start_document(self, pdf_path: Path) -> None:
        self._current_doc = pdf_path.name
        self._timings = {}
        self._logger.debug(f"=== DOCUMENT START: {self._current_doc} ===")

    def log_extraction(self, raw_metadata_dict: dict) -> None:
        parts = []
        for key in ("document_type", "document_title", "issuing_party", "issue_date", "confidence",
                     "total_amount", "total_amount_currency", "reasoning"):
            val = raw_metadata_dict.get(key)
            if val is not None:
                parts.append(f'{key}="{val}"' if isinstance(val, str) else f"{key}={val}")
        self._logger.debug(f"[RAW] {' '.join(parts)}")

    def log_normalization(self, field: str, raw: str, normalized: str) -> None:
        self._logger.debug(f"[NORM] {field}: '{raw}' -> '{normalized}'")

    def log_timing(self, operation: str, seconds: float) -> None:
        self._timings[operation] = seconds
        self._logger.debug(f"[TIMING] {operation}: {seconds:.2f}s")

    def log_final(self, metadata_dict: dict) -> None:
        parts = []
        for key in ("document_type", "issuing_party", "date_issued",
                     "total_amount", "total_amount_currency", "document_title"):
            val = metadata_dict.get(key)
            if val is not None:
                parts.append(f"{key}={val}")
        self._logger.debug(f"[FINAL] {' '.join(parts)}")

    def log_llm_usage(self, model: str, prompt_tokens: int, completion_tokens: int) -> None:
        self._logger.debug(f"[LLM-USAGE] model={model} prompt_tokens={prompt_tokens} completion_tokens={completion_tokens}")

    def log_qr_extraction(self, qr_type: str, extracted_fields: dict, page_number: int = 0) -> None:
        field_parts = []
        for key, val in extracted_fields.items():
            if val is not None:
                field_parts.append(f'{key}="{val}"' if isinstance(val, str) else f"{key}={val}")
        fields_str = " ".join(field_parts) if field_parts else "(no fields)"
        self._logger.debug(f"[QR-EXTRACT] type={qr_type} page={page_number} {fields_str}")

    def log_qr_not_found(self) -> None:
        self._logger.debug("[QR-EXTRACT] No QR codes found")

    def log_qr_merge(self, field: str, qr_value, llm_value) -> None:
        self._logger.debug(f"[QR-MERGE] {field}: QR={qr_value} overrides LLM={llm_value}")

    def log_qr_skip(self, excluded_fields: set[str]) -> None:
        self._logger.debug(f"[QR-SKIP] Excluding from LLM: {sorted(excluded_fields)}")

    def log_nif_cache_hit(self, nif: str, issuer: str) -> None:
        self._logger.debug(f"[NIF-CACHE-HIT] {nif} → {issuer}")

    def log_nif_web_lookup(self, nif: str, issuer: str) -> None:
        self._logger.debug(f"[NIF-WEB-LOOKUP] {nif} → {issuer} (cached)")

    def log_nif_not_found(self, nif: str, reason: str) -> None:
        self._logger.debug(f"[NIF-NOT-FOUND] {nif} ({reason})")

    def log_nif_web_error(self, nif: str, error: str) -> None:
        doc_context = f" [{self._current_doc}]" if self._current_doc else ""
        self._logger.warning(f"[NIF-WEB-ERROR]{doc_context} {nif} → {error}")

    def log_nif_enrichment(self, nif: str, official_issuer: str, normalized_issuer: str) -> None:
        self._logger.debug(f"[NIF-ENRICH] {nif} → {official_issuer} → {normalized_issuer}")

    def end_document(self, status: str = "SUCCESS") -> None:
        total = sum(self._timings.values())
        if total > 0:
            self._logger.debug(f"[TIMING] total: {total:.2f}s")
        self._logger.debug(f"=== DOCUMENT END: {self._current_doc} -- {status} ===")
        self._current_doc = None
        self._timings = {}
