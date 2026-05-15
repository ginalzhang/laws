"""
Fuzzy voter roll matcher using rapidfuzz.

The voter roll is loaded once at startup and indexed for fast lookup.
We use a two-stage match:
  1. rapidfuzz.process.extract on the pre-built search_key index
     (last + first + street number + street name) — fast top-N candidates
  2. Separate name_score and address_score for transparency in the review UI
  3. Final confidence = weighted average (60% name, 40% address)

Loading from CSV:
  Minimum required columns (case-insensitive):
    voter_id, last_name, first_name, street_address
  Optional but helpful:
    city, state, zip_code
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

from ..models import NormalizedSignature, VoterMatch

# Weights for composite confidence score
NAME_WEIGHT    = 0.60
ADDRESS_WEIGHT = 0.40

# How many candidates to score in stage 2
TOP_N = 5


def _build_search_key(row: pd.Series) -> str:
    last    = str(row.get("last_name", "")).lower().strip()
    first   = str(row.get("first_name", "")).lower().strip()
    street  = str(row.get("street_address", "")).lower().strip()
    # Extract just the house number and street name (drop city/state/zip noise)
    parts = street.split()
    street_short = " ".join(parts[:4]) if len(parts) > 4 else street
    return " ".join(filter(None, [last, first, street_short]))


class VoterMatcher:
    """
    Load once, query many times.

        matcher = VoterMatcher.from_csv("voter_roll.csv")
        match = matcher.match(normalized_sig)
    """

    def __init__(self, df: pd.DataFrame):
        self._df = df.copy()
        # Pre-build the search keys column once
        self._df["_search_key"] = self._df.apply(_build_search_key, axis=1)
        self._keys = self._df["_search_key"].tolist()

    @classmethod
    def from_csv(cls, path: str | Path, **read_csv_kwargs) -> "VoterMatcher":
        df = pd.read_csv(path, dtype=str, **read_csv_kwargs).fillna("")
        # Normalise column names to lowercase
        df.columns = [c.lower().strip() for c in df.columns]
        required = {"voter_id", "last_name", "first_name", "street_address"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(
                f"Voter roll CSV is missing required columns: {missing}\n"
                f"Found: {list(df.columns)}"
            )
        return cls(df)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame) -> "VoterMatcher":
        return cls(df)

    def candidates(self, sig: NormalizedSignature, limit: int = 3) -> list[VoterMatch]:
        """Return the top voter roll candidates for staff review."""
        if not self._keys:
            return []

        query = sig.search_key
        if not query.strip():
            return []

        # Stage 1: fast top-N on combined key
        candidates = process.extract(
            query,
            self._keys,
            scorer=fuzz.token_sort_ratio,
            limit=max(TOP_N, limit),
        )

        scored: list[VoterMatch] = []

        for _matched_key, _score, idx in candidates:
            row = self._df.iloc[idx]

            # Stage 2: separate name and address scores
            voter_name_key = f"{row.get('last_name', '')} {row.get('first_name', '')}".lower().strip()
            voter_addr_key = str(row.get("street_address", "")).lower().strip()

            sig_name_key = f"{sig.last_name} {sig.first_name}".strip()
            sig_addr_key = sig.street.strip()

            name_score    = fuzz.token_sort_ratio(sig_name_key, voter_name_key)
            address_score = fuzz.token_sort_ratio(sig_addr_key, voter_addr_key)

            # Boost address score slightly if zip codes match exactly
            if sig.zip_code and sig.zip_code == str(row.get("zip_code", "")):
                address_score = min(100, address_score + 5)

            confidence = NAME_WEIGHT * name_score + ADDRESS_WEIGHT * address_score

            scored.append(VoterMatch(
                voter_id=str(row["voter_id"]),
                voter_name=f"{row.get('first_name', '')} {row.get('last_name', '')}".strip(),
                voter_address=str(row.get("street_address", "")),
                confidence=round(confidence, 1),
                name_score=round(name_score, 1),
                address_score=round(address_score, 1),
            ))

        scored.sort(key=lambda match: match.confidence, reverse=True)
        return scored[:limit]

    def match(self, sig: NormalizedSignature) -> VoterMatch | None:
        """Return the best voter roll match, or None if the roll is empty."""
        candidates = self.candidates(sig, limit=1)
        return candidates[0] if candidates else None
