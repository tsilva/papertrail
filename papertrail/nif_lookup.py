"""NIF lookup cache for Portuguese tax number → issuer name resolution."""

import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from papertrail.cache_base import BaseYamlCache
from papertrail.logging_utils import get_logger

logger = get_logger('nif_lookup')


class NIFLookupCache(BaseYamlCache):
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
    _data_key = "nif_to_issuer"

    def __init__(self, cache_path: Optional[Path] = None):
        """Initialize the NIF lookup cache.

        Args:
            cache_path: Path to the YAML cache file. Defaults to config/nif_cache.yaml
        """
        default_path = Path(__file__).parent.parent / "config" / "nif_cache.yaml"
        super().__init__(cache_path, default_path)

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

        # TIER 1: Cache lookup
        if nif in self._cache:
            return self._cache[nif], "cache", None

        # TIER 2: Web scraping lookup
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
