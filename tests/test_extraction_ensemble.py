"""Tests for the multi-agent ensemble extractor."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from petition_verifier.extraction import extract_row_ensemble
from petition_verifier.extraction.ensemble import _validate, consensus_from_extractions


def _fake_response(payload: dict) -> SimpleNamespace:
    """Build a fake Messages API response whose first content block is JSON."""
    block = SimpleNamespace(type="text", text=json.dumps(payload))
    return SimpleNamespace(content=[block])


class _FakeClient:
    """Mock AsyncAnthropic — returns canned JSON per call.

    Call order matches the conductor: (1) Haiku vision, (2) Sonnet vision,
    (3) Sonnet reconciliation.
    """

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(create=AsyncMock(side_effect=self._reply))

    async def _reply(self, **kwargs):
        self.calls.append(kwargs)
        return _fake_response(self._responses.pop(0))


def _blank_image() -> Image.Image:
    return Image.new("RGB", (400, 60), color="white")


# ── Conductor / pipeline ─────────────────────────────────────────────────────

def test_pipeline_returns_reconciled_fields():
    haiku = {
        "name": "John Smith", "name_confidence": 75,
        "address": "123 Oak Ave", "address_confidence": 80,
        "city": "Pasadena", "city_confidence": 85,
        "zip_code": "91101", "zip_confidence": 90,
    }
    sonnet = {
        "name": "John Smith", "name_confidence": 85,
        "address": "123 Oak Avenue", "address_confidence": 88,
        "city": "Pasadena", "city_confidence": 90,
        "zip_code": "91101", "zip_confidence": 92,
    }
    reconciled = {
        "name": "John Smith",
        "address": "123 Oak Avenue",
        "city": "Pasadena",
        "zip_code": "91101",
        "disagreements": [],
    }
    client = _FakeClient([haiku, sonnet, reconciled])

    result = extract_row_ensemble(_blank_image(), client=client)

    assert result["name"] == "John Smith"
    assert result["address"] == "123 Oak Avenue"
    assert result["city"] == "Pasadena"
    assert result["zip_code"] == "91101"
    assert result["disagreements"] == []
    assert result["validation_flags"] == []
    assert result["unreliable_fields"] == ["address"]
    assert result["extractions"]["haiku"] == haiku
    assert result["extractions"]["sonnet"] == sonnet
    assert len(client.calls) == 3


def test_pipeline_handles_non_json_extraction():
    """A vision agent returning non-JSON should not crash the pipeline."""
    client = _FakeClient([
        {"name": "Jane Doe", "name_confidence": 70, "address": "5 Elm St",
         "address_confidence": 70, "city": "LA", "city_confidence": 70,
         "zip_code": "90001", "zip_confidence": 70},
        {"name": "Jane Doe", "name_confidence": 70, "address": "5 Elm St",
         "address_confidence": 70, "city": "LA", "city_confidence": 70,
         "zip_code": "90001", "zip_confidence": 70},
        {"name": "Jane Doe", "address": "5 Elm St", "city": "LA",
         "zip_code": "90001", "disagreements": []},
    ])
    # Override the first call to return invalid JSON
    invalid = SimpleNamespace(content=[SimpleNamespace(type="text", text="not json {{{")])
    client.messages.create = AsyncMock(side_effect=[
        invalid,
        _fake_response({"name": "Jane Doe", "name_confidence": 70, "address": "5 Elm St",
                        "address_confidence": 70, "city": "LA", "city_confidence": 70,
                        "zip_code": "90001", "zip_confidence": 70}),
        _fake_response({"name": "Jane Doe", "address": "5 Elm St", "city": "LA",
                        "zip_code": "90001", "disagreements": []}),
    ])

    result = extract_row_ensemble(_blank_image(), client=client)
    assert result["name"] == "Jane Doe"
    assert result["extractions"]["haiku"] == {}  # non-JSON → empty


def test_validate_flags_missing_fields():
    flags = _validate({"name": "", "address": "", "city": "", "zip_code": ""})
    assert "name_missing" in flags
    assert "address_missing" in flags
    assert "city_missing" in flags
    assert "zip_missing" in flags


def test_validate_flags_single_word_name():
    flags = _validate({
        "name": "Cher", "address": "1 Main St", "city": "LA", "zip_code": "90001",
    })
    assert "name_missing_first_or_last" in flags


def test_validate_flags_address_without_street_number():
    flags = _validate({
        "name": "Jane Doe", "address": "Main Street", "city": "LA", "zip_code": "90001",
    })
    assert "address_missing_street_number" in flags


def test_validate_flags_non_california_zip():
    # New York zip
    flags = _validate({
        "name": "Jane Doe", "address": "1 Main St", "city": "LA", "zip_code": "10001",
    })
    assert "zip_invalid_california" in flags
    # 96500 is in the 96xxx block but ABOVE California's 96199 ceiling
    flags = _validate({
        "name": "Jane Doe", "address": "1 Main St", "city": "LA", "zip_code": "96500",
    })
    assert "zip_invalid_california" in flags
    # 96100 is a valid CA zip (boundary of 961xx range)
    flags = _validate({
        "name": "Jane Doe", "address": "1 Main St", "city": "LA", "zip_code": "96100",
    })
    assert "zip_invalid_california" not in flags


def test_validate_passes_clean_row():
    flags = _validate({
        "name": "Jane Doe", "address": "123 Oak Ave", "city": "Pasadena", "zip_code": "91101",
    })
    assert flags == []


def test_pipeline_calls_use_distinct_models():
    """Vision agent 1 should hit Haiku, agent 2 should hit Sonnet, reconciler Sonnet."""
    haiku = {"name": "X X", "name_confidence": 50, "address": "1 St",
             "address_confidence": 50, "city": "LA", "city_confidence": 50,
             "zip_code": "90001", "zip_confidence": 50}
    sonnet = dict(haiku)
    reconciled = {"name": "X X", "address": "1 St", "city": "LA",
                  "zip_code": "90001", "disagreements": []}
    client = _FakeClient([haiku, sonnet, reconciled])

    extract_row_ensemble(_blank_image(), client=client)

    models = [c["model"] for c in client.calls]
    assert models[0] == "claude-haiku-4-5"
    assert models[1] == "claude-sonnet-4-6"
    assert models[2] == "claude-sonnet-4-6"


def test_pipeline_uses_prompt_caching():
    """Each call should set cache_control on the system prompt for prefix caching."""
    haiku = {"name": "X X", "name_confidence": 50, "address": "1 St",
             "address_confidence": 50, "city": "LA", "city_confidence": 50,
             "zip_code": "90001", "zip_confidence": 50}
    client = _FakeClient([haiku, dict(haiku),
                          {"name": "X X", "address": "1 St", "city": "LA",
                           "zip_code": "90001", "disagreements": []}])

    extract_row_ensemble(_blank_image(), client=client)

    for call in client.calls:
        system = call["system"]
        assert isinstance(system, list)
        assert system[0].get("cache_control") == {"type": "ephemeral"}


def test_consensus_from_extractions_marks_disagreements_unreliable():
    haiku = {
        "name": "Reggie Ellison", "name_confidence": 84,
        "address": "123 Oak Ave", "address_confidence": 82,
        "city": "Pasadena", "city_confidence": 80,
        "zip_code": "91101", "zip_confidence": 90,
    }
    sonnet = {
        "name": "Boyce Shelton", "name_confidence": 86,
        "address": "123 Oak Ave", "address_confidence": 80,
        "city": "Pasadena", "city_confidence": 80,
        "zip_code": "91101", "zip_confidence": 90,
    }

    consensus = consensus_from_extractions(haiku, sonnet)

    assert consensus["name"] == ""
    assert consensus["address"] == "123 Oak Ave"
    assert consensus["city"] == "Pasadena"
    assert consensus["zip_code"] == "91101"
    assert consensus["unreliable_fields"] == ["name"]


def test_consensus_from_extractions_requires_minimum_confidence():
    haiku = {
        "name": "Jane Doe", "name_confidence": 45,
        "address": "1 Main St", "address_confidence": 80,
        "city": "LA", "city_confidence": 80,
        "zip_code": "90001", "zip_confidence": 80,
    }
    sonnet = {
        "name": "Jane Doe", "name_confidence": 85,
        "address": "1 Main St", "address_confidence": 80,
        "city": "LA", "city_confidence": 80,
        "zip_code": "90001", "zip_confidence": 80,
    }

    consensus = consensus_from_extractions(haiku, sonnet)

    assert consensus["name"] == ""
    assert consensus["unreliable_fields"] == ["name"]
