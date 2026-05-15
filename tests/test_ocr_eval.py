from __future__ import annotations

from pathlib import Path

from petition_verifier.extraction.eval import (
    flatten_ground_truth,
    load_ground_truth,
    score_predictions,
)


EVAL_TRUTH = Path(__file__).parent / "fixtures" / "ocr_eval" / "ground_truth.json"


def test_ocr_eval_fixture_has_minimum_labeled_sheets():
    sheets = load_ground_truth(EVAL_TRUTH)

    assert len(sheets) >= 10
    for sheet in sheets:
        assert sheet["sheet_id"]
        assert sheet["rows"]
        for row in sheet["rows"]:
            assert row["name"]
            assert row["address"]


def test_ocr_eval_scores_field_accuracy_and_hallucinations():
    sheets = load_ground_truth(EVAL_TRUTH)
    truth = flatten_ground_truth(sheets)
    first_key = next(iter(truth))
    predictions = {
        key: {
            "name": row["name"],
            "address": row["address"],
            "low_confidence_fields": [],
        }
        for key, row in truth.items()
    }
    predictions[first_key] = {
        "name": "Boyce Shelton",
        "address": truth[first_key]["address"],
        "low_confidence_fields": ["name"],
    }

    scores = score_predictions(truth, predictions)

    assert scores["total_rows"] == len(truth)
    assert scores["field_accuracy"]["address"] == 1.0
    assert scores["unreliable_counts"]["name"] == 1
    assert scores["fabricated_names"] == 0
