"""NIF lookup cache for Portuguese tax number → issuer name resolution."""

import re
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from papertrail.logging_utils import get_logger
from papertrail.yaml_utils import load_yaml, save_yaml

logger = get_logger('nif_lookup')


class NIFLookupCache:
    """Cache NIF → issuer name mappings with web scraping fallback.

    Two-tier lookup following existing patterns:
    1. TIER 1 - Cache: Check config/nif_cache.yaml for known NIF → issuer mappings
    2. TIER 2 - Web: If cache miss, scrape nif.pt public URL, cache result for reuse

    Example cache structure:
        nif_to_issuer:
          "ISSUER-TAX-ID": "Anthropic, PBC"
          "510751334": "Google Cloud Portugal"
    """

    WEB_URL = "https://www.nif.pt/{nif}/"

    def __init__(self, cache_path: Optional[Path] = None):
        """Initialize the NIF lookup cache.

        Args:
            cache_path: Path to the YAML cache file. Defaults to config/nif_cache.yaml
        """
        self._lock = threading.Lock()
        if cache_path is None:
            cache_path = Path(__file__).parent.parent / "config" / "nif_cache.yaml"
        self.path = cache_path
        self._cache: dict[str, str] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            data = load_yaml(self.path)
            self._cache = data.get("nif_to_issuer", {})
        except Exception:
            self._cache = {}

    @staticmethod
    def normalize_nif(nif: str) -> str:
        """Normalize NIF by stripping country prefix and whitespace.

        Args:
            nif: Raw NIF string (e.g., "PTISSUER-TAX-ID", " ISSUER-TAX-ID ")

        Returns:
            Normalized NIF (e.g., "ISSUER-TAX-ID")
        """
        nif = nif.strip()
        # Strip common country prefixes
        for prefix in ("PT", "pt"):
            if nif.startswith(prefix):
                nif = nif[len(prefix):]
        return nif.strip()

    @staticmethod
    def validate_nif_checksum(nif: str) -> bool:
        """Validate Portuguese NIF using mod-11 check digit algorithm.

        Algorithm:
        1. Multiply first 8 digits by weights [9,8,7,6,5,4,3,2]
        2. Sum the products
        3. Compute: remainder = sum mod 11
        4. Check digit = 0 if remainder in (0,1) else 11 - remainder
        5. Validate 9th digit equals check digit

        Args:
            nif: 9-digit NIF string (already validated for format)

        Returns:
            True if checksum is valid
        """
        if len(nif) != 9 or not nif.isdigit():
            return False

        weights = [9, 8, 7, 6, 5, 4, 3, 2]
        total = sum(int(nif[i]) * weights[i] for i in range(8))
        remainder = total % 11

        expected_check = 0 if remainder in (0, 1) else 11 - remainder
        return int(nif[8]) == expected_check

    @staticmethod
    def is_portuguese_nif(nif: str) -> bool:
        """Check if a tax number is a valid Portuguese NIF.

        Portuguese NIFs:
        - Are 9 digits starting with 1-9
        - May have "PT" prefix
        - Must NOT have other country prefixes (IE, DE, SE, ES, etc.)
        - Must pass mod-11 checksum validation

        Args:
            nif: Raw tax number string

        Returns:
            True if this is a valid Portuguese NIF
        """
        nif = nif.strip().upper()

        # Strip PT prefix if present
        if nif.startswith("PT"):
            nif = nif[2:].strip()

        # Reject if starts with any other country code (2 uppercase letters)
        if len(nif) >= 2 and nif[:2].isalpha():
            return False

        # Basic format check: 9 digits starting with 1-9
        if not (len(nif) == 9 and nif.isdigit() and nif[0] != '0'):
            return False

        # Checksum validation
        return NIFLookupCache.validate_nif_checksum(nif)

    def save(self) -> None:
        """Save cache to YAML file if dirty (thread-safe)."""
        with self._lock:
            if not self._dirty:
                return
            save_yaml(self.path, {"nif_to_issuer": self._cache})
            self._dirty = False

    def __len__(self) -> int:
        return len(self._cache)

    def get(self, nif: str) -> Optional[str]:
        """Get cached issuer name for a NIF.

        Args:
            nif: The tax number (will be normalized)

        Returns:
            Cached issuer name if found, None otherwise
        """
        nif = self.normalize_nif(nif)
        return self._cache.get(nif)

    def set(self, nif: str, issuer: str) -> None:
        """Cache a NIF → issuer mapping.

        Args:
            nif: The tax number (will be normalized)
            issuer: The issuer/company name
        """
        nif = self.normalize_nif(nif)
        with self._lock:
            if self._cache.get(nif) != issuer:
                self._cache[nif] = issuer
                self._dirty = True

    def lookup(self, nif: str) -> tuple[Optional[str], str, Optional[str]]:
        """Look up issuer name by NIF (TIER 1 cache, TIER 2 web scraping).

        Args:
            nif: The tax number (will be normalized)

        Returns:
            Tuple of (issuer_name, source, error_message) where source is one of:
            - "cache" for TIER 1 hit
            - "web" for TIER 2 web lookup
            - "not_found" if not found on nif.pt
            - "web_error" if web scraping failed
            error_message is None unless source is "web_error"
        """
        nif = self.normalize_nif(nif)

        # TIER 1: Cache lookup (lock-protected)
        with self._lock:
            if nif in self._cache:
                return self._cache[nif], "cache", None

        # TIER 2: Web scraping lookup (outside lock - can be slow)
        issuer, error = self._web_lookup(nif)
        if issuer:
            self.set(nif, issuer)
            return issuer, "web", None

        if error:
            return None, "web_error", error

        return None, "not_found", None

    def _web_lookup(self, nif: str) -> tuple[Optional[str], Optional[str]]:
        """Scrape company name from nif.pt public URL.

        Args:
            nif: Normalized NIF (no country prefix)

        Returns:
            Tuple of (company_name, error_message). Both can be None if not found without error.
        """
        url = self.WEB_URL.format(nif=nif)

        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                html = response.read().decode('utf-8')

            # Find <span class='search-title'>Company Name</span>
            # Handle both single and double quotes
            match = re.search(r'class=[\'"]search-title[\'"][^>]*>([^<]+)</span>', html, re.DOTALL)
            if match:
                company_name = match.group(1).strip()
                return company_name, None

            # No results found
            return None, None

        except urllib.error.URLError as e:
            return None, f"network error: {e}"
        except Exception as e:
            return None, f"error: {e}"
