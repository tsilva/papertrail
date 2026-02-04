"""Unified pattern matching utilities for file selection.

Provides auto-detection between glob patterns and regex patterns,
and consistent matching behavior across tasks.
"""

import fnmatch
import re
from typing import Callable

# Indicators that a pattern is regex (not glob)
# These are regex-specific constructs that don't appear in glob patterns
REGEX_INDICATORS = (
    r'\d',   # digit class
    r'\w',   # word class
    r'\s',   # whitespace class
    r'\b',   # word boundary
    r'\B',   # non-word boundary
    r'^',    # start anchor
    r'$',    # end anchor
    r'+',    # one or more quantifier
    r'{',    # counted quantifier
    r'}',    # counted quantifier
    r'(?',   # non-capturing group / lookahead
    r'\A',   # absolute start
    r'\Z',   # absolute end
    r'|',    # alternation
)


def is_regex_pattern(pattern: str) -> bool:
    """Detect if pattern uses regex-specific syntax.

    Args:
        pattern: The pattern string to analyze.

    Returns:
        True if pattern appears to be a regex, False for glob/exact.

    Examples:
        >>> is_regex_pattern("*invoice*.pdf")
        False
        >>> is_regex_pattern("2025-01-\\d{2}")
        True
        >>> is_regex_pattern("invoice")
        False
        >>> is_regex_pattern("foo|bar")
        True
    """
    return any(indicator in pattern for indicator in REGEX_INDICATORS)


def make_matcher(pattern: str, use_search: bool = False) -> Callable[[str], bool]:
    """Create a matcher function from a pattern.

    Auto-detects whether the pattern is glob or regex, and returns
    an appropriate matcher function.

    Args:
        pattern: Glob or regex pattern.
        use_search: If True (for regex), use search() for partial match
                   instead of fullmatch(). Glob always uses full match.

    Returns:
        A callable that takes a filename and returns True if it matches.

    Examples:
        >>> matcher = make_matcher("*invoice*.pdf")
        >>> matcher("2025-01-01 - invoice - vendor.pdf")
        True
        >>> matcher = make_matcher("2025-01-\\d{2}", use_search=True)
        >>> matcher("2025-01-15 - invoice - vendor.pdf")
        True
    """
    if is_regex_pattern(pattern):
        compiled = re.compile(pattern)
        if use_search:
            return lambda name: bool(compiled.search(name))
        else:
            return lambda name: bool(compiled.fullmatch(name))
    else:
        return lambda name: fnmatch.fnmatch(name, pattern)
