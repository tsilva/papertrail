"""NIF lookup cache for Portuguese tax number → issuer name resolution."""

import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from papertrail.yaml_utils import load_yaml, save_yaml
from papertrail.logging_utils import get_logger

logger = get_logger('nif_lookup')


class NIFLookupCache:
    """Cache NIF → issuer name mappings with web scraping fallback.

    Two-tier lookup following existing patterns:
    1. TIER 1 - Cache: Check config/nif_cache.yaml for known NIF → issuer mappings
    2. TIER 2 - Web: If cache miss, scrape nif.pt public URL, cache result for reuse

    Example cache structure:
        nif_to_issuer:
          "503782467": "Anthropic, PBC"
          "510751334": "Google Cloud Portugal"
    """

    WEB_URL = "https://www.nif.pt/{nif}/"

    def __init__(self, cache_path: Optional[Path] = None):
        """Initialize the NIF lookup cache.

        Args:
            cache_path: Path to the YAML cache file. Defaults to config/nif_cache.yaml
        """
        if cache_path is None:
            cache_path = Path(__file__).parent.parent / "config" / "nif_cache.yaml"
        self.cache_path = cache_path
        self._cache: dict[str, str] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        """Load cache from YAML file."""
        try:
            data = load_yaml(self.cache_path)
            self._cache = data.get("nif_to_issuer", {})
        except Exception:
            self._cache = {}

    def save(self) -> None:
        """Save cache to YAML file if dirty."""
        if not self._dirty:
            return
        save_yaml(self.cache_path, {"nif_to_issuer": self._cache})
        self._dirty = False

    @staticmethod
    def normalize_nif(nif: str) -> str:
        """Normalize NIF by stripping country prefix and whitespace.

        Args:
            nif: Raw NIF string (e.g., "PT503782467", " 503782467 ")

        Returns:
            Normalized NIF (e.g., "503782467")
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

    def lookup(self, nif: str) -> tuple[Optional[str], str]:
        """Look up issuer name by NIF (TIER 1 cache, TIER 2 web scraping).

        Args:
            nif: The tax number (will be normalized)

        Returns:
            Tuple of (issuer_name, source) where source is one of:
            - "cache" for TIER 1 hit
            - "web" for TIER 2 web lookup
            - "not_found" if not found on nif.pt
            - "web_error" if web scraping failed
        """
        nif = self.normalize_nif(nif)

        # TIER 1: Cache lookup
        if nif in self._cache:
            return self._cache[nif], "cache"

        # TIER 2: Web scraping lookup
        issuer = self._web_lookup(nif)
        if issuer:
            self.set(nif, issuer)
            return issuer, "web"

        return None, "not_found"

    def _web_lookup(self, nif: str) -> Optional[str]:
        """Scrape company name from nif.pt public URL.

        Args:
            nif: Normalized NIF (no country prefix)

        Returns:
            Company name if found, None otherwise
        """
        url = self.WEB_URL.format(nif=nif)

        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                html = response.read().decode('utf-8')

            # Find <h2 class="search-title"><span>Company Name</span></h2>
            match = re.search(r'class="search-title"[^>]*>.*?<span>([^<]+)</span>', html, re.DOTALL)
            if match:
                company_name = match.group(1).strip()
                logger.debug(f"[NIF-WEB] {nif} → {company_name}")
                return company_name

            # No results found
            logger.debug(f"[NIF-WEB] {nif} → no results")
            return None

        except urllib.error.URLError as e:
            logger.warning(f"[NIF-WEB] {nif} → network error: {e}")
            return None
        except Exception as e:
            logger.warning(f"[NIF-WEB] {nif} → error: {e}")
            return None

    def __len__(self) -> int:
        """Return number of cached entries."""
        return len(self._cache)
