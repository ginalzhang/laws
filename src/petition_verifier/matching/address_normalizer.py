"""
Name and address normalization before fuzzy matching.

Goals:
  - Strip noise that trips up string comparison (punctuation, suffixes, apt numbers)
  - Split raw_name into first + last
  - Parse raw_address into street / city / state / zip using usaddress
  - Build a single search_key = "{last} {first} {street}" for primary matching
"""
from __future__ import annotations

import re
import unicodedata

import usaddress

from ..models import ExtractedSignature, NormalizedSignature

# Common name suffixes to strip before comparison
_NAME_SUFFIXES = re.compile(
    r"\b(jr\.?|sr\.?|ii|iii|iv|esq\.?|phd|md|dds)\b",
    re.IGNORECASE,
)

# Standardize street type abbreviations for better matching
_STREET_ABBREVS = {
    "street": "st", "avenue": "ave", "boulevard": "blvd", "drive": "dr",
    "road": "rd", "lane": "ln", "court": "ct", "place": "pl",
    "circle": "cir", "terrace": "ter", "way": "way",
}

# usaddress label → our field
_ADDR_MAP = {
    "AddressNumber":            "number",
    "StreetNamePreDirectional": "pre_dir",
    "StreetName":               "street_name",
    "StreetNamePostType":       "street_type",
    "StreetNamePostDirectional":"post_dir",
    "PlaceName":                "city",
    "StateName":                "state",
    "ZipCode":                  "zip",
}


def _clean(text: str) -> str:
    """Lowercase, strip accents, remove punctuation."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _split_name(raw: str) -> tuple[str, str]:
    """
    Split 'John Smith' or 'Smith, John' into (first, last).
    Returns ("", cleaned_raw) if we can't split confidently.
    """
    raw = _NAME_SUFFIXES.sub("", raw).strip()

    # Check for "Last, First" format BEFORE _clean() strips the comma
    if "," in raw:
        parts = [_clean(p) for p in raw.split(",", 1)]
        return parts[1].strip(), parts[0].strip()  # last, first → first, last

    raw = _clean(raw)

    parts = raw.split()
    if len(parts) >= 2:
        return parts[0], parts[-1]

    return "", raw


def _parse_address(raw: str) -> dict[str, str]:
    """Return street / city / state / zip from a raw address string."""
    result = {"number": "", "street_name": "", "street_type": "",
              "city": "", "state": "", "zip": "", "pre_dir": "", "post_dir": ""}
    try:
        tagged, _ = usaddress.tag(raw)
        for label, value in tagged.items():
            key = _ADDR_MAP.get(label)
            if key:
                result[key] = _clean(value)
    except usaddress.RepeatedLabelError:
        # Fall back to raw string if usaddress can't parse
        result["street_name"] = _clean(raw)

    # Normalize street type
    st_type = result["street_type"]
    result["street_type"] = _STREET_ABBREVS.get(st_type, st_type)

    return result


def normalize_signature(sig: ExtractedSignature) -> NormalizedSignature:
    first, last = _split_name(sig.raw_name)
    addr = _parse_address(sig.raw_address)

    street = " ".join(filter(None, [
        addr["number"],
        addr["pre_dir"],
        addr["street_name"],
        addr["street_type"],
        addr["post_dir"],
    ])).strip()

    # Primary search key: last + first + street number + street name
    # Omitting city/state/zip to reduce penalty for abbreviation differences
    search_key = " ".join(filter(None, [last, first, addr["number"], addr["street_name"]]))

    return NormalizedSignature(
        line_number=sig.line_number,
        page=sig.page,
        first_name=first,
        last_name=last,
        street=street,
        city=addr["city"],
        state=addr["state"],
        zip_code=addr["zip"],
        date=_clean(sig.raw_date),
        signature_present=sig.signature_present,
        signature_bbox=sig.signature_bbox,
        search_key=search_key,
    )
