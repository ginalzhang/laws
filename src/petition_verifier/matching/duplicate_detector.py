"""
Duplicate signature detection within a single batch (one PDF or one project).

Two-pass approach:
  1. Exact key match — same normalized (last, first, street_number) seen twice
  2. Near-duplicate via rapidfuzz — catches OCR variants of the same person
     (e.g. "Jon Smith 123 Main" vs "John Smith 123 Maine")

Usage:
    detector = DuplicateDetector()
    for sig in normalized_sigs:
        dupe_of = detector.check(sig)
        if dupe_of is not None:
            # sig is a duplicate of line dupe_of
"""
from __future__ import annotations

import re

from rapidfuzz import fuzz

from ..models import NormalizedSignature

# Similarity threshold for near-duplicate detection (0-100)
NEAR_DUPE_THRESHOLD = 92


def _exact_key(sig: NormalizedSignature) -> str:
    """Stable key for exact duplicate detection."""
    # Use voter_id if we have it, otherwise fall back to name+street number
    street_num = re.match(r"^\d+", sig.street)
    num = street_num.group(0) if street_num else ""
    return f"{sig.last_name}|{sig.first_name}|{num}".lower()


class DuplicateDetector:
    """Stateful detector — reset between projects, not between pages."""

    def __init__(self):
        self._exact: dict[str, int] = {}      # exact_key → line_number
        self._seen: list[tuple[str, int]] = [] # (search_key, line_number)

    def reset(self):
        self._exact.clear()
        self._seen.clear()

    def check(self, sig: NormalizedSignature) -> int | None:
        """
        Return the line_number of a previous signature this duplicates,
        or None if it's new.

        Side effect: registers the signature if it's not a duplicate.
        """
        key = _exact_key(sig)

        # 1. Exact key match
        if key in self._exact:
            return self._exact[key]

        # 2. Near-duplicate via fuzzy match on search key
        query = sig.search_key
        if query.strip():
            for prev_key, prev_line in self._seen:
                score = fuzz.token_sort_ratio(query, prev_key)
                if score >= NEAR_DUPE_THRESHOLD:
                    return prev_line

        # Not a duplicate — register it
        self._exact[key] = sig.line_number
        self._seen.append((query, sig.line_number))
        return None
