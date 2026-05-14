"""Unit tests for the matching layer — no PDF or Tesseract needed."""
import pandas as pd
import pytest

from petition_verifier.matching import DuplicateDetector, VoterMatcher, normalize_signature
from petition_verifier.models import ExtractedSignature, VerificationStatus


# ── fixtures ──────────────────────────────────────────────────────────────────

VOTER_ROLL = pd.DataFrame([
    {"voter_id": "V001", "first_name": "Jane",  "last_name": "Smith",   "street_address": "123 Main St",   "city": "Springfield", "state": "CA", "zip_code": "90210"},
    {"voter_id": "V002", "first_name": "John",  "last_name": "Doe",     "street_address": "456 Oak Ave",   "city": "Springfield", "state": "CA", "zip_code": "90210"},
    {"voter_id": "V003", "first_name": "Maria", "last_name": "Garcia",  "street_address": "789 Pine Rd",   "city": "Riverside",   "state": "CA", "zip_code": "92501"},
    {"voter_id": "V004", "first_name": "Bob",   "last_name": "Johnson", "street_address": "321 Elm Dr",    "city": "Springfield", "state": "CA", "zip_code": "90211"},
    {"voter_id": "V005", "first_name": "Alice", "last_name": "Brown",   "street_address": "555 Maple Blvd","city": "Riverside",   "state": "CA", "zip_code": "92502"},
])


def _make_sig(name: str, address: str, line: int = 1) -> ExtractedSignature:
    return ExtractedSignature(
        line_number=line, page=1,
        raw_name=name, raw_address=address,
        signature_present=True,
    )


# ── normalizer ────────────────────────────────────────────────────────────────

def test_normalize_splits_name():
    sig = _make_sig("Jane Smith", "123 Main St, Springfield, CA 90210")
    n = normalize_signature(sig)
    assert n.first_name == "jane"
    assert n.last_name  == "smith"


def test_normalize_comma_name():
    sig = _make_sig("Smith, Jane", "123 Main St")
    n = normalize_signature(sig)
    assert n.first_name == "jane"
    assert n.last_name  == "smith"


def test_normalize_strips_suffix():
    sig = _make_sig("John Doe Jr.", "456 Oak Ave")
    n = normalize_signature(sig)
    assert "jr" not in n.last_name


def test_search_key_non_empty():
    sig = _make_sig("Maria Garcia", "789 Pine Rd, Riverside CA")
    n = normalize_signature(sig)
    assert n.search_key.strip() != ""


# ── voter matcher ─────────────────────────────────────────────────────────────

def test_exact_match():
    matcher = VoterMatcher.from_dataframe(VOTER_ROLL)
    sig = _make_sig("Jane Smith", "123 Main St Springfield CA")
    n   = normalize_signature(sig)
    m   = matcher.match(n)
    assert m is not None
    assert m.voter_id == "V001"
    assert m.confidence >= 85


def test_fuzzy_name_match():
    matcher = VoterMatcher.from_dataframe(VOTER_ROLL)
    # OCR noise: "Smyth" instead of "Smith"
    sig = _make_sig("Jane Smyth", "123 Main St Springfield CA")
    n   = normalize_signature(sig)
    m   = matcher.match(n)
    assert m is not None
    assert m.voter_id == "V001"


def test_low_confidence_for_wrong_person():
    matcher = VoterMatcher.from_dataframe(VOTER_ROLL)
    sig = _make_sig("Xyz Qwerty", "999 Nowhere Blvd Faketown ZZ")
    n   = normalize_signature(sig)
    m   = matcher.match(n)
    assert m is None or m.confidence < 70


# ── duplicate detector ────────────────────────────────────────────────────────

def test_exact_duplicate_detected():
    detector = DuplicateDetector()
    s1 = normalize_signature(_make_sig("Jane Smith", "123 Main St", line=1))
    s2 = normalize_signature(_make_sig("Jane Smith", "123 Main St", line=2))
    assert detector.check(s1) is None   # first occurrence
    assert detector.check(s2) == 1      # duplicate of line 1


def test_near_duplicate_detected():
    detector = DuplicateDetector()
    s1 = normalize_signature(_make_sig("John Doe", "456 Oak Ave",  line=1))
    # OCR variant
    s2 = normalize_signature(_make_sig("John Do",  "456 Oak Ave",  line=2))
    assert detector.check(s1) is None
    result = detector.check(s2)
    assert result == 1


def test_different_people_not_duplicates():
    detector = DuplicateDetector()
    s1 = normalize_signature(_make_sig("Jane Smith",  "123 Main St", line=1))
    s2 = normalize_signature(_make_sig("Maria Garcia","789 Pine Rd", line=2))
    assert detector.check(s1) is None
    assert detector.check(s2) is None


def test_same_name_and_house_number_on_different_streets_not_duplicate():
    detector = DuplicateDetector()
    s1 = normalize_signature(_make_sig("John Smith", "123 Main St, Springfield CA", line=1))
    s2 = normalize_signature(_make_sig("John Smith", "123 Oak Ave, Springfield CA", line=2))
    assert detector.check(s1) is None
    assert detector.check(s2) is None


def test_detector_resets():
    detector = DuplicateDetector()
    s = normalize_signature(_make_sig("Jane Smith", "123 Main St", line=1))
    detector.check(s)
    detector.reset()
    s2 = normalize_signature(_make_sig("Jane Smith", "123 Main St", line=2))
    assert detector.check(s2) is None  # reset means no memory of line 1
