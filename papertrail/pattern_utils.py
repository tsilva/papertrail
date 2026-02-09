"""Pattern matching utilities for file selection."""

import fnmatch
import re
from typing import Callable

REGEX_INDICATORS = (
    r'\d', r'\w', r'\s', r'\b', r'\B',
    r'^', r'$', r'+', r'{', r'}',
    r'(?', r'\A', r'\Z', r'|',
)


def is_regex_pattern(pattern: str) -> bool:
    """Detect if pattern uses regex-specific syntax."""
    return any(indicator in pattern for indicator in REGEX_INDICATORS)


def make_matcher(pattern: str, use_search: bool = False) -> Callable[[str], bool]:
    """Create a matcher function from a glob or regex pattern."""
    if is_regex_pattern(pattern):
        compiled = re.compile(pattern)
        if use_search:
            return lambda name: bool(compiled.search(name))
        else:
            return lambda name: bool(compiled.fullmatch(name))
    else:
        if use_search and not any(c in pattern for c in '*?['):
            return lambda name: pattern in name
        return lambda name: fnmatch.fnmatch(name, pattern)
