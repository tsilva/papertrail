"""NIF lookup cache for Portuguese tax number → issuer name resolution."""

import re
import threading
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional

from papertrail.logging_utils import get_logger
from papertrail.utils import load_yaml, save_yaml

logger = get_logger('nif_lookup')


class NIFLookupCache:
    """Cache NIF -> issuer name mappings (TIER 1: cache, TIER 2: nif.pt web scraping)."""

    WEB_URL = "https://www.nif.pt/{nif}/"

    def __init__(self, cache_path: Optional[Path] = None):
        self._lock = threading.Lock()
        if cache_path is None:
            cache_path = Path(__file__).parent.parent / ".cache" / "nif_cache.yaml"
        self.path = cache_path
        self._cache: dict[str, str] = {}
        self._normalized: dict[str, str] = {}
        self._dirty = False
        self._load()

    def _load(self) -> None:
        try:
            data = load_yaml(self.path)
            self._cache = data.get("nif_to_issuer", {})
            self._normalized = data.get("nif_to_normalized", {})
        except Exception:
            self._cache = {}
            self._normalized = {}

    @staticmethod
    def normalize_nif(nif: str) -> str:
        """Normalize NIF by stripping country prefix and whitespace."""
        nif = nif.strip()
        for prefix in ("PT", "pt"):
            if nif.startswith(prefix):
                nif = nif[len(prefix):]
        return nif.strip()

    @staticmethod
    def validate_nif_checksum(nif: str) -> bool:
        """Validate Portuguese NIF using mod-11 check digit algorithm."""
        if len(nif) != 9 or not nif.isdigit():
            return False

        weights = [9, 8, 7, 6, 5, 4, 3, 2]
        total = sum(int(nif[i]) * weights[i] for i in range(8))
        remainder = total % 11

        expected_check = 0 if remainder in (0, 1) else 11 - remainder
        return int(nif[8]) == expected_check

    @staticmethod
    def is_portuguese_nif(nif: str) -> bool:
        """Check if a tax number is a valid Portuguese NIF (9 digits, mod-11 checksum)."""
        nif = nif.strip().upper()
        if nif.startswith("PT"):
            nif = nif[2:].strip()
        if len(nif) >= 2 and nif[:2].isalpha():
            return False
        if not (len(nif) == 9 and nif.isdigit() and nif[0] != '0'):
            return False
        return NIFLookupCache.validate_nif_checksum(nif)

    def save(self) -> None:
        with self._lock:
            if not self._dirty:
                return
            data = {"nif_to_issuer": self._cache}
            if self._normalized:
                data["nif_to_normalized"] = self._normalized
            save_yaml(self.path, data)
            self._dirty = False

    def __len__(self) -> int:
        return len(self._cache)

    def get(self, nif: str) -> Optional[str]:
        nif = self.normalize_nif(nif)
        return self._cache.get(nif)

    def set(self, nif: str, issuer: str) -> None:
        nif = self.normalize_nif(nif)
        with self._lock:
            if self._cache.get(nif) != issuer:
                self._cache[nif] = issuer
                self._dirty = True

    def get_normalized(self, nif: str) -> Optional[str]:
        nif = self.normalize_nif(nif)
        return self._normalized.get(nif)

    def set_normalized(self, nif: str, normalized: str) -> None:
        nif = self.normalize_nif(nif)
        with self._lock:
            if self._normalized.get(nif) != normalized:
                self._normalized[nif] = normalized
                self._dirty = True

    def lookup(self, nif: str) -> tuple[Optional[str], str, Optional[str]]:
        """Look up issuer by NIF. Returns (issuer_name, source, error_message)."""
        nif = self.normalize_nif(nif)
        with self._lock:
            if nif in self._cache:
                return self._cache[nif], "cache", None

        issuer, error = self._web_lookup(nif)
        if issuer:
            self.set(nif, issuer)
            return issuer, "web", None

        if error:
            return None, "web_error", error

        return None, "not_found", None

    def _web_lookup(self, nif: str) -> tuple[Optional[str], Optional[str]]:
        """Scrape company name from nif.pt. Returns (company_name, error_message)."""
        url = self.WEB_URL.format(nif=nif)

        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                html = response.read().decode('utf-8')

            match = re.search(r'class=[\'"]search-title[\'"][^>]*>([^<]+)</span>', html, re.DOTALL)
            if match:
                company_name = match.group(1).strip()
                return company_name, None

            return None, None

        except urllib.error.URLError as e:
            return None, f"network error: {e}"
        except Exception as e:
            return None, f"error: {e}"
