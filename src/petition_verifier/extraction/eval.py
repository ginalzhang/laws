"""Small OCR eval-set helpers.

The eval runner compares ExtractedSignature-like predictions against
hand-labeled rows. It deliberately operates at the ExtractedSignature boundary
so tests do not mock external OCR APIs.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


REQUIRED_FIELDS = ("name", "address")


def _norm(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def load_ground_truth(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    sheets = data.get("sheets", [])
    if len(sheets) < 10:
        raise ValueError("OCR eval set must contain at least 10 labeled sheets")
    return sheets


def flatten_ground_truth(sheets: list[dict[str, Any]]) -> dict[tuple[str, int], dict[str, str]]:
    rows: dict[tuple[str, int], dict[str, str]] = {}
    for sheet in sheets:
        sheet_id = str(sheet["sheet_id"])
        for row in sheet.get("rows", []):
            rows[(sheet_id, int(row["line_number"]))] = {
                "name": str(row.get("name", "")),
                "address": str(row.get("address", "")),
            }
    return rows


def score_predictions(
    ground_truth: dict[tuple[str, int], dict[str, str]],
    predictions: dict[tuple[str, int], dict[str, Any]],
) -> dict[str, Any]:
    totals = {field: 0 for field in REQUIRED_FIELDS}
    correct = {field: 0 for field in REQUIRED_FIELDS}
    unreliable = {field: 0 for field in REQUIRED_FIELDS}
    fabricated_names = 0

    for key, truth in ground_truth.items():
        prediction = predictions.get(key, {})
        low_fields = set(prediction.get("low_confidence_fields") or [])
        for field in REQUIRED_FIELDS:
            totals[field] += 1
            if field in low_fields:
                unreliable[field] += 1
                continue
            if _norm(str(prediction.get(field, ""))) == _norm(truth[field]):
                correct[field] += 1
        if (
            "name" not in low_fields
            and _norm(str(prediction.get("name", "")))
            and _norm(str(prediction.get("name", ""))) != _norm(truth["name"])
        ):
            fabricated_names += 1

    field_accuracy = {
        field: correct[field] / max(1, totals[field])
        for field in REQUIRED_FIELDS
    }
    return {
        "total_rows": len(ground_truth),
        "field_accuracy": field_accuracy,
        "name_accuracy": field_accuracy["name"],
        "address_accuracy": field_accuracy["address"],
        "unreliable_counts": unreliable,
        "fabricated_names": fabricated_names,
    }
