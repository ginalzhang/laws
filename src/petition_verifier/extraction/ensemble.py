"""Multi-agent ensemble extraction for petition signature rows.

Pipeline (one petition row image → final fields):

  1. Preprocess the row image (boost contrast, ensure RGB).
  2. Vision agent 1 — Haiku, "messy handwriting" framing — extracts
     name/address/city/zip with per-field confidence (0-100). Runs in
     parallel with #3.
  3. Vision agent 2 — Sonnet, "document digitization" framing — does the
     same on the same image with a different prompt angle.
  4. Reconciliation agent — Sonnet — picks the best value per field and
     flags disagreements.
  5. Deterministic validator — checks zip is a valid CA zip, name has
     first+last, address has a street number, and (if a county is given)
     the city actually belongs to that county.

System prompts are cached via prompt caching, so per-row API cost amortizes
across the petition.

Usage:

    from petition_verifier.extraction import extract_row_ensemble
    result = extract_row_ensemble(row_pil_image, county="Los Angeles")
    # result["name"] / ["address"] / ["city"] / ["zip_code"]
    # result["validation_flags"] — list of issues found
    # result["disagreements"] — fields where the two vision agents differed
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import re
from typing import Any

from PIL import Image, ImageEnhance

logger = logging.getLogger(__name__)

# ── Models (user-specified) ───────────────────────────────────────────────────
VISION_MODEL_FAST = "claude-haiku-4-5"
VISION_MODEL_CAREFUL = "claude-sonnet-4-6"
RECONCILE_MODEL = "claude-sonnet-4-6"

# CA zip range: 90000-96199
_CA_ZIP_RE = re.compile(r"^(9[0-5]\d{3}|96[01]\d{2})$")
_FIELD_VALUE_KEYS = {
    "name": "name",
    "address": "address",
    "city": "city",
    "zip": "zip_code",
}
_FIELD_CONFIDENCE_KEYS = {
    "name": "name_confidence",
    "address": "address_confidence",
    "city": "city_confidence",
    "zip": "zip_confidence",
}


# ── JSON Schemas for structured outputs ───────────────────────────────────────

_EXTRACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Full name as written, preserve casing"},
        "name_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "address": {"type": "string", "description": "Street number + street name"},
        "address_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "city": {"type": "string"},
        "city_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
        "zip_code": {"type": "string", "description": "5 digits"},
        "zip_confidence": {"type": "integer", "minimum": 0, "maximum": 100},
    },
    "required": [
        "name", "name_confidence",
        "address", "address_confidence",
        "city", "city_confidence",
        "zip_code", "zip_confidence",
    ],
    "additionalProperties": False,
}

_RECONCILED_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "address": {"type": "string"},
        "city": {"type": "string"},
        "zip_code": {"type": "string"},
        "disagreements": {
            "type": "array",
            "items": {"type": "string", "enum": ["name", "address", "city", "zip"]},
        },
    },
    "required": ["name", "address", "city", "zip_code", "disagreements"],
    "additionalProperties": False,
}


# ── System prompts (stable — cacheable) ───────────────────────────────────────

_PROMPT_FAST = """You are extracting handwritten data from a single row of a petition signature sheet.

The image is one row from a petition. Each row contains a name, street address, city, and zip code, filled in by hand on a printed form. The handwriting is messy — printed and cursive mix freely, characters overlap, ink varies.

Read every field. Try hard.

Rules:
- NEVER return an empty field if anything is visible. Make your best guess and use a low confidence score.
- Only return empty if the cell is genuinely blank or fully unreadable (smudge, fold, missing).
- Confidence is a 0-100 integer per field: 80+ = clearly legible, 50-79 = readable but ambiguous, 1-49 = best guess.
- Names: keep capitalization as written. Don't expand initials or correct spelling.
- Addresses: include the street number; drop apt/unit if hard to read.
- Zip: 5 digits only.
"""

_PROMPT_CAREFUL = """This is a document digitization task.

You are transcribing a single row from a printed petition signature sheet, filled in by hand. Approach it like a careful archivist transcribing a historical record: read what is actually written — including faint, smudged, or cursive characters — and don't substitute what you'd expect.

Look at printed AND cursive handwriting. Petitioners often switch styles within the same row.

Return four fields per row: name, address (street number + street name), city, zip code.

Rules:
- Confidence per field is a 0-100 integer: 80+ = clearly legible, 50-79 = readable but ambiguous, 1-49 = best guess.
- If a field is unclear, return your best guess at low confidence rather than leaving it blank.
- Only return empty when the cell is truly blank — not just hard to read.
- Names: preserve casing as written.
- Addresses: include the street number; omit hard-to-read apt numbers.
- Zip: exactly 5 digits if you can read them.
"""

_PROMPT_RECONCILE = """You reconcile two independent extractions of the same petition row.

Input: two JSON objects (extraction A from Haiku, extraction B from Sonnet), each with name/address/city/zip and per-field confidence (0-100). Pick the best value for each field.

Per-field priority:
1. If both are non-empty and agree (case- and whitespace-insensitive) — return that value.
2. If only one is non-empty — return the non-empty one.
3. If both are non-empty and disagree:
   a. Prefer the value with materially higher confidence (>= 20-point gap).
   b. Otherwise prefer the value that looks more like a real entry: proper capitalization, plausible spelling, valid zip format (5 digits, 9xxxx for CA).
   c. For names: prefer the longer/more complete value if it's a strict superset (e.g. "John A Smith" over "John Smith").
   d. For addresses: prefer the one with a street number.
4. List any field in `disagreements` where both extractions returned non-empty but materially different values, OR where both returned empty.

Output ONLY the reconciled values + the disagreements list. Do not invent fields.
"""


# ── Image utilities ───────────────────────────────────────────────────────────

def _preprocess(img: Image.Image) -> Image.Image:
    """Boost contrast and ensure RGB before sending to the API."""
    if img.mode != "RGB":
        img = img.convert("RGB")
    return ImageEnhance.Contrast(img).enhance(1.4)


def _img_b64(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return base64.standard_b64encode(buf.getvalue()).decode("utf-8")


# ── API calls ─────────────────────────────────────────────────────────────────

def _extract_text(response: Any) -> str:
    """Pull the first text block from a Messages API response."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return block.text
    return ""


async def _call_extraction(
    client: Any,
    model: str,
    system_prompt: str,
    img_b64_str: str,
) -> dict[str, Any]:
    """Run one vision agent on the row image."""
    response = await client.messages.create(
        model=model,
        max_tokens=512,
        system=[{
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": img_b64_str,
                    },
                },
                {
                    "type": "text",
                    "text": "Extract the four fields from this petition row. Return ONLY JSON.",
                },
            ],
        }],
        output_config={"format": {"type": "json_schema", "schema": _EXTRACTION_SCHEMA}},
    )
    text = _extract_text(response)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Extraction agent (%s) returned non-JSON: %r", model, text[:200])
        return {}


async def _call_reconcile(
    client: Any,
    extraction_a: dict[str, Any],
    extraction_b: dict[str, Any],
) -> dict[str, Any]:
    """Reconcile two extractions into a final answer."""
    user_text = (
        f"Extraction A (Haiku):\n{json.dumps(extraction_a, indent=2)}\n\n"
        f"Extraction B (Sonnet):\n{json.dumps(extraction_b, indent=2)}"
    )
    response = await client.messages.create(
        model=RECONCILE_MODEL,
        max_tokens=512,
        system=[{
            "type": "text",
            "text": _PROMPT_RECONCILE,
            "cache_control": {"type": "ephemeral"},
        }],
        messages=[{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        output_config={"format": {"type": "json_schema", "schema": _RECONCILED_SCHEMA}},
    )
    text = _extract_text(response)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.warning("Reconciler returned non-JSON: %r", text[:200])
        return {
            "name": "",
            "address": "",
            "city": "",
            "zip_code": "",
            "disagreements": ["name", "address", "city", "zip"],
        }


# ── Validator (deterministic, no API call) ───────────────────────────────────

def _validate(reconciled: dict[str, Any], county: str = "") -> list[str]:
    """Return a list of validation flag strings (empty list = all checks passed)."""
    flags: list[str] = []

    name = (reconciled.get("name") or "").strip()
    if not name:
        flags.append("name_missing")
    elif len(name.split()) < 2:
        flags.append("name_missing_first_or_last")

    addr = (reconciled.get("address") or "").strip()
    if not addr:
        flags.append("address_missing")
    elif not re.match(r"^\d", addr):
        flags.append("address_missing_street_number")

    city = (reconciled.get("city") or "").strip()
    if not city:
        flags.append("city_missing")
    elif county:
        # ca_counties.city_in_county may not exist on every branch — guard import
        try:
            from ..ingestion.ca_counties import city_in_county
        except ImportError:
            pass
        else:
            if not city_in_county(city, county):
                flags.append("city_county_mismatch")

    zip_code = (reconciled.get("zip_code") or "").strip()
    if not zip_code:
        flags.append("zip_missing")
    elif not _CA_ZIP_RE.match(zip_code):
        flags.append("zip_invalid_california")

    return flags


# ── Strict consensus gate ────────────────────────────────────────────────────

def _normalise_for_compare(value: str, *, field: str) -> str:
    value = (value or "").strip().lower()
    if field == "zip":
        return re.sub(r"\D", "", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _field_agrees(extraction_a: dict[str, Any], extraction_b: dict[str, Any], field: str) -> bool:
    value_key = _FIELD_VALUE_KEYS[field]
    left = _normalise_for_compare(str(extraction_a.get(value_key) or ""), field=field)
    right = _normalise_for_compare(str(extraction_b.get(value_key) or ""), field=field)
    return bool(left and right and left == right)


def consensus_from_extractions(
    extraction_a: dict[str, Any],
    extraction_b: dict[str, Any],
    *,
    min_confidence: int = 50,
) -> dict[str, Any]:
    """Require two extraction agents to agree field-by-field.

    Disagreements are returned as unreliable fields instead of being reconciled
    into a plausible-looking final value. The caller may still retain raw OCR
    values for audit/debug, but UI code must hide unreliable fields.
    """
    consensus: dict[str, Any] = {
        "name": "",
        "address": "",
        "city": "",
        "zip_code": "",
        "unreliable_fields": [],
    }
    for field, value_key in _FIELD_VALUE_KEYS.items():
        conf_key = _FIELD_CONFIDENCE_KEYS[field]
        left_conf = int(extraction_a.get(conf_key) or 0)
        right_conf = int(extraction_b.get(conf_key) or 0)
        if (
            _field_agrees(extraction_a, extraction_b, field)
            and left_conf >= min_confidence
            and right_conf >= min_confidence
        ):
            consensus[value_key] = (extraction_a.get(value_key) or extraction_b.get(value_key) or "").strip()
        else:
            consensus["unreliable_fields"].append(field)
    return consensus


# ── Conductor (orchestrates the whole pipeline) ──────────────────────────────

async def _extract_row_async(
    row_img: Image.Image,
    county: str = "",
    client: Any = None,
) -> dict[str, Any]:
    """Async core. Pass `client` to inject a mock for testing."""
    if client is None:
        from anthropic import AsyncAnthropic
        client = AsyncAnthropic()

    img = _preprocess(row_img)
    img_b64_str = _img_b64(img)

    # Run both vision agents in parallel — they look at the same image with
    # different prompts; one cache miss apiece on the first row, then warm.
    a_task = _call_extraction(client, VISION_MODEL_FAST, _PROMPT_FAST, img_b64_str)
    b_task = _call_extraction(client, VISION_MODEL_CAREFUL, _PROMPT_CAREFUL, img_b64_str)
    extraction_a, extraction_b = await asyncio.gather(a_task, b_task)

    reconciled = await _call_reconcile(client, extraction_a, extraction_b)
    flags = _validate(reconciled, county)
    consensus = consensus_from_extractions(extraction_a, extraction_b)

    return {
        "name": reconciled.get("name", ""),
        "address": reconciled.get("address", ""),
        "city": reconciled.get("city", ""),
        "zip_code": reconciled.get("zip_code", ""),
        "consensus": consensus,
        "unreliable_fields": consensus["unreliable_fields"],
        "extractions": {"haiku": extraction_a, "sonnet": extraction_b},
        "disagreements": reconciled.get("disagreements", []),
        "validation_flags": flags,
    }


def extract_row_ensemble(
    row_img: Image.Image,
    county: str = "",
    client: Any = None,
) -> dict[str, Any]:
    """Sync entry point. Runs the multi-agent pipeline for one row.

    Returns a dict with: name, address, city, zip_code, extractions (per-agent
    raw output), disagreements (fields the two vision agents disputed), and
    validation_flags (deterministic plausibility issues).
    """
    return asyncio.run(_extract_row_async(row_img, county=county, client=client))
