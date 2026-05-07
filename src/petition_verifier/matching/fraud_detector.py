"""
Fraud detection for petition sheets — no voter roll required.

The main pattern we're looking for is one person filling in multiple lines
themselves (fabricating signatures). This shows up as:

  SAME_CITY          — most entries share the same city, suggesting one person
                       used their own city/neighborhood for every line
  NEARBY_ADDRESS     — different names at addresses within a few house numbers
                       of each other on the same street (one person's block)
  CONSECUTIVE_ADDRS  — house numbers that go 101, 102, 103, 104 … in order
  DUPLICATE_NAME     — essentially the same full name appears twice
  DUPLICATE_ADDRESS  — essentially the same street address appears twice
  SAME_DATE          — every filled entry has the identical date (someone
                       stamped the same date on all lines at once)
  NO_SIGNATURE       — name/address filled but no signature ink detected
  BLANK_LINE         — row is completely empty
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional

from rapidfuzz import fuzz

from ..models import ExtractedSignature, NormalizedSignature


# ── Flag codes ────────────────────────────────────────────────────────────────

SAME_CITY         = "same_city"
NEARBY_ADDRESS    = "nearby_address"
CONSECUTIVE_ADDRS = "consecutive_addresses"
DUPLICATE_NAME    = "duplicate_name"
DUPLICATE_ADDRESS = "duplicate_address"
SAME_DATE         = "same_date"
NO_SIGNATURE      = "no_signature"
BLANK_LINE        = "blank_line"
SAME_HANDWRITING  = "same_handwriting"

# Thresholds
_NAME_SIM_THRESHOLD    = 92    # fuzzy — allows minor OCR noise
_ADDRESS_SIM_THRESHOLD = 88    # slightly looser to catch near-duplicate streets
_NEARBY_HOUSE_GAP      = 10    # house numbers within 10 = same block
_CONSECUTIVE_WINDOW    = 4     # run of 4+ consecutive numbers = suspicious
_CITY_DOMINANT_PCT     = 0.70  # 70%+ of filled lines share one city = flagged
_HANDWRITING_SIM       = 0.97  # cosine similarity above this = likely same writer


@dataclass
class FraudFlag:
    code: str
    description: str
    related_lines: List[int] = field(default_factory=list)


@dataclass
class LineFraudResult:
    line_number: int
    page: int
    raw_name: str
    raw_address: str
    raw_date: str
    signature_present: bool
    flags: List[FraudFlag] = field(default_factory=list)
    ocr_confidence: Optional[float] = None

    @property
    def is_flagged(self) -> bool:
        return bool(self.flags)

    @property
    def flag_codes(self) -> List[str]:
        return [f.code for f in self.flags]


@dataclass
class FraudScanResult:
    project_id: str
    source_path: str
    total_lines: int
    flagged_lines: int
    lines: List[LineFraudResult] = field(default_factory=list)

    def summary(self) -> dict:
        flag_counts: Counter = Counter()
        for line in self.lines:
            for f in line.flags:
                flag_counts[f.code] += 1
        blank_count = flag_counts.pop(BLANK_LINE, 0)
        filled_lines = self.total_lines - blank_count
        suspicious = sum(
            1 for line in self.lines
            if any(f.code != BLANK_LINE for f in line.flags)
        )
        return {
            "project_id":       self.project_id,
            "source_path":      self.source_path,
            "total_lines":      self.total_lines,
            "filled_lines":     filled_lines,
            "blank_lines":      blank_count,
            "suspicious_lines": suspicious,
            "clean_lines":      filled_lines - suspicious,
            "flag_counts":      dict(flag_counts),
        }


# ── Address helpers ───────────────────────────────────────────────────────────

def _house_number(address: str) -> Optional[int]:
    m = re.match(r"^\s*(\d+)", address.strip())
    return int(m.group(1)) if m else None


def _street_name(address: str) -> str:
    """Address with house number stripped, lowercased."""
    return re.sub(r"^\s*\d+\s*", "", address).lower().strip()


def _normalize_date(raw: str) -> str:
    """Strip whitespace/punctuation for date comparison."""
    return re.sub(r"[\s\-/.]", "", raw.strip().lower())


# ── Analyzer ──────────────────────────────────────────────────────────────────

class FraudAnalyzer:

    def analyze(
        self,
        extracted: list[ExtractedSignature],
        normalized: list[NormalizedSignature],
        project_id: str = "",
        source_path: str = "",
    ) -> FraudScanResult:

        results: list[LineFraudResult] = [
            LineFraudResult(
                line_number=e.line_number,
                page=e.page,
                raw_name=e.raw_name,
                raw_address=e.raw_address,
                raw_date=e.raw_date,
                signature_present=e.signature_present,
                ocr_confidence=e.ocr_confidence,
            )
            for e in extracted
        ]

        self._flag_blank(results)
        self._flag_no_signature(results)
        self._flag_same_city(results, normalized)
        self._flag_nearby_and_consecutive(results)
        self._flag_duplicate_names(results, normalized)
        self._flag_duplicate_addresses(results, normalized)
        self._flag_same_date(results)
        self._flag_similar_handwriting(results, extracted)

        flagged = sum(1 for r in results if r.is_flagged)
        return FraudScanResult(
            project_id=project_id,
            source_path=source_path,
            total_lines=len(results),
            flagged_lines=flagged,
            lines=results,
        )

    # ── Checks ────────────────────────────────────────────────────────────────

    def _flag_blank(self, results: list[LineFraudResult]) -> None:
        for r in results:
            if not r.raw_name.strip() and not r.raw_address.strip():
                r.flags.append(FraudFlag(
                    code=BLANK_LINE,
                    description="Empty row — no name or address detected",
                ))

    def _flag_no_signature(self, results: list[LineFraudResult]) -> None:
        for r in results:
            if (r.raw_name.strip() or r.raw_address.strip()) and not r.signature_present:
                r.flags.append(FraudFlag(
                    code=NO_SIGNATURE,
                    description="Name/address filled in but no signature found",
                ))

    def _flag_same_city(
        self,
        results: list[LineFraudResult],
        normalized: list[NormalizedSignature],
    ) -> None:
        """
        If most filled entries share one city, flag them all.
        A real petition sheet collected in a park or shopping center will have
        signers from many different cities. If everything says 'Los Angeles' it
        suggests one person invented the addresses.
        """
        norm_map = {n.line_number: n for n in normalized}
        filled = [
            (r, norm_map[r.line_number])
            for r in results
            if r.line_number in norm_map
            and (r.raw_name.strip() or r.raw_address.strip())
            and BLANK_LINE not in r.flag_codes
        ]
        if len(filled) < 4:
            return  # too few lines to draw conclusions

        city_counts: Counter = Counter()
        for _, n in filled:
            c = n.city.strip().lower()
            if c:
                city_counts[c] += 1

        if not city_counts:
            return

        top_city, top_count = city_counts.most_common(1)[0]
        if top_count / len(filled) >= _CITY_DOMINANT_PCT:
            flagged_lines = [
                r.line_number for r, n in filled
                if n.city.strip().lower() == top_city
            ]
            for r, _ in filled:
                if r.line_number in flagged_lines:
                    _add_if_missing(
                        r, SAME_CITY,
                        f'{top_count} of {len(filled)} entries share city '
                        f'"{top_city.title()}" — possible fabrication',
                        [ln for ln in flagged_lines if ln != r.line_number],
                    )

    def _flag_nearby_and_consecutive(self, results: list[LineFraudResult]) -> None:
        """
        Group entries by street name.  Within each group:
        - Flag any pair whose house numbers are within _NEARBY_HOUSE_GAP of each
          other (different names, same block → suspicious).
        - Also flag runs of _CONSECUTIVE_WINDOW+ perfectly consecutive numbers.
        """
        from collections import defaultdict

        by_street: dict[str, list[tuple[int, LineFraudResult]]] = defaultdict(list)
        for r in results:
            if BLANK_LINE in r.flag_codes:
                continue
            num = _house_number(r.raw_address)
            if num is None:
                continue
            street = _street_name(r.raw_address)
            if not street:
                continue
            by_street[street].append((num, r))

        for street, entries in by_street.items():
            if len(entries) < 2:
                continue
            entries.sort(key=lambda x: x[0])
            nums = [n for n, _ in entries]

            # Nearby pairs (within _NEARBY_HOUSE_GAP)
            for i in range(len(entries)):
                for j in range(i + 1, len(entries)):
                    gap = abs(nums[j] - nums[i])
                    if gap == 0:
                        continue  # exact duplicate — caught by duplicate_address
                    if gap <= _NEARBY_HOUSE_GAP:
                        ri, rj = entries[i][1], entries[j][1]
                        _add_if_missing(
                            ri, NEARBY_ADDRESS,
                            f"Address is {gap} door(s) from line {rj.line_number} "
                            f"on the same street — may be from the same block",
                            [rj.line_number],
                        )
                        _add_if_missing(
                            rj, NEARBY_ADDRESS,
                            f"Address is {gap} door(s) from line {ri.line_number} "
                            f"on the same street — may be from the same block",
                            [ri.line_number],
                        )

            # Consecutive runs
            for start in range(len(nums) - _CONSECUTIVE_WINDOW + 1):
                window = nums[start: start + _CONSECUTIVE_WINDOW]
                if window == list(range(window[0], window[0] + _CONSECUTIVE_WINDOW)):
                    run_lines = [entries[start + k][1].line_number
                                 for k in range(_CONSECUTIVE_WINDOW)]
                    for k in range(_CONSECUTIVE_WINDOW):
                        r = entries[start + k][1]
                        _add_if_missing(
                            r, CONSECUTIVE_ADDRS,
                            f"House numbers are perfectly consecutive across "
                            f"lines {run_lines} — strongly suggests fabrication",
                            [ln for ln in run_lines if ln != r.line_number],
                        )

    def _flag_duplicate_names(
        self,
        results: list[LineFraudResult],
        normalized: list[NormalizedSignature],
    ) -> None:
        norm_map = {n.line_number: n for n in normalized}
        pairs = [
            (r, f"{norm_map[r.line_number].first_name} {norm_map[r.line_number].last_name}".strip())
            for r in results
            if r.line_number in norm_map
        ]
        for i, (ri, name_i) in enumerate(pairs):
            if not name_i:
                continue
            for rj, name_j in pairs[i + 1:]:
                if not name_j:
                    continue
                sim = fuzz.token_sort_ratio(name_i, name_j)
                if sim >= _NAME_SIM_THRESHOLD:
                    _add_if_missing(ri, DUPLICATE_NAME,
                        f'Same person as line {rj.line_number} '
                        f'("{name_j}", {sim:.0f}% match)',
                        [rj.line_number])
                    _add_if_missing(rj, DUPLICATE_NAME,
                        f'Same person as line {ri.line_number} '
                        f'("{name_i}", {sim:.0f}% match)',
                        [ri.line_number])

    def _flag_duplicate_addresses(
        self,
        results: list[LineFraudResult],
        normalized: list[NormalizedSignature],
    ) -> None:
        norm_map = {n.line_number: n for n in normalized}
        pairs = [
            (r, norm_map[r.line_number].street.strip())
            for r in results
            if r.line_number in norm_map
        ]
        for i, (ri, addr_i) in enumerate(pairs):
            if not addr_i:
                continue
            for rj, addr_j in pairs[i + 1:]:
                if not addr_j:
                    continue
                sim = fuzz.token_sort_ratio(addr_i, addr_j)
                if sim >= _ADDRESS_SIM_THRESHOLD:
                    _add_if_missing(ri, DUPLICATE_ADDRESS,
                        f'Same address as line {rj.line_number} ({sim:.0f}% match)',
                        [rj.line_number])
                    _add_if_missing(rj, DUPLICATE_ADDRESS,
                        f'Same address as line {ri.line_number} ({sim:.0f}% match)',
                        [ri.line_number])

    def _flag_same_date(self, results: list[LineFraudResult]) -> None:
        """
        If every filled line shares the exact same date, it suggests one person
        wrote all the dates at once rather than signers writing their own.
        """
        dated = [
            r for r in results
            if r.raw_date.strip() and BLANK_LINE not in r.flag_codes
        ]
        if len(dated) < 4:
            return

        dates = [_normalize_date(r.raw_date) for r in dated]
        if not dates[0]:
            return

        if all(d == dates[0] for d in dates):
            all_lines = [r.line_number for r in dated]
            for r in dated:
                _add_if_missing(
                    r, SAME_DATE,
                    f'All {len(dated)} entries share the same date '
                    f'("{dated[0].raw_date.strip()}") — dates may have been '
                    f'filled in by one person',
                    [ln for ln in all_lines if ln != r.line_number],
                )


    def _flag_similar_handwriting(
        self,
        results: list[LineFraudResult],
        extracted: list[ExtractedSignature],
    ) -> None:
        by_line = {r.line_number: r for r in results}

        # Claude backend: use visual handwriting assessment directly
        claude_pairs: set[tuple[int, int]] = set()
        for e in extracted:
            if e.same_handwriting_as:
                for other_ln in e.same_handwriting_as:
                    pair = (min(e.line_number, other_ln), max(e.line_number, other_ln))
                    claude_pairs.add(pair)

        for ln_a, ln_b in claude_pairs:
            desc = (
                f"Handwriting on lines {ln_a} and {ln_b} appears visually identical "
                f"— likely written by the same person"
            )
            if ln_a in by_line:
                _add_if_missing(by_line[ln_a], SAME_HANDWRITING, desc, [ln_b])
            if ln_b in by_line:
                _add_if_missing(by_line[ln_b], SAME_HANDWRITING, desc, [ln_a])

        if claude_pairs:
            return  # Claude assessment takes precedence over pixel vectors

        # Vision backend fallback: cosine similarity on pixel density vectors
        import math
        vecs = {e.line_number: e.handwriting_vector for e in extracted if e.handwriting_vector}
        if len(vecs) < 2:
            return

        line_nums = list(vecs.keys())
        for i in range(len(line_nums)):
            for j in range(i + 1, len(line_nums)):
                a, b = vecs[line_nums[i]], vecs[line_nums[j]]
                dot   = sum(x * y for x, y in zip(a, b))
                mag_a = math.sqrt(sum(x * x for x in a))
                mag_b = math.sqrt(sum(x * x for x in b))
                sim   = dot / (mag_a * mag_b + 1e-9)
                if sim >= _HANDWRITING_SIM:
                    ln_a, ln_b = line_nums[i], line_nums[j]
                    desc = (
                        f"Handwriting on lines {ln_a} and {ln_b} appears very similar "
                        f"(similarity {sim:.2f}) — may have been written by the same person"
                    )
                    if ln_a in by_line:
                        _add_if_missing(by_line[ln_a], SAME_HANDWRITING, desc, [ln_b])
                    if ln_b in by_line:
                        _add_if_missing(by_line[ln_b], SAME_HANDWRITING, desc, [ln_a])


# ── Helper ────────────────────────────────────────────────────────────────────

def _add_if_missing(
    result: LineFraudResult,
    code: str,
    description: str,
    related: list[int],
) -> None:
    if code not in result.flag_codes:
        result.flags.append(FraudFlag(
            code=code,
            description=description,
            related_lines=related,
        ))
