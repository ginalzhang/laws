from __future__ import annotations

from petition_verifier.matching import normalize_signature
from petition_verifier.matching.fraud_detector import (
    BLANK_LINE,
    DUPLICATE_ADDRESS,
    DUPLICATE_NAME,
    NO_SIGNATURE,
    FraudAnalyzer,
)
from petition_verifier.models import ExtractedSignature


def _sig(line: int, name: str, address: str, signature_present: bool = True) -> ExtractedSignature:
    return ExtractedSignature(
        line_number=line,
        page=1,
        raw_name=name,
        raw_address=address,
        raw_date="05/14/2026",
        signature_present=signature_present,
    )


def _analyze(signatures: list[ExtractedSignature]):
    return FraudAnalyzer().analyze(
        signatures,
        [normalize_signature(sig) for sig in signatures],
        project_id="fraud-test",
        source_path="fixture.pdf",
    )


def test_fraud_detector_flags_blank_and_missing_signature_rows():
    result = _analyze([
        _sig(1, "", "", signature_present=False),
        _sig(2, "Jane Smith", "123 Main St", signature_present=False),
    ])

    by_line = {line.line_number: line.flag_codes for line in result.lines}
    assert BLANK_LINE in by_line[1]
    assert NO_SIGNATURE in by_line[2]
    assert result.summary()["blank_lines"] == 1
    assert result.summary()["suspicious_lines"] == 1


def test_fraud_detector_flags_duplicate_names_and_addresses():
    result = _analyze([
        _sig(1, "Jane Smith", "123 Main St"),
        _sig(2, "Jane Smith", "123 Main St"),
        _sig(3, "Maria Garcia", "789 Pine Rd"),
    ])

    by_line = {line.line_number: line.flag_codes for line in result.lines}
    assert DUPLICATE_NAME in by_line[1]
    assert DUPLICATE_NAME in by_line[2]
    assert DUPLICATE_ADDRESS in by_line[1]
    assert DUPLICATE_ADDRESS in by_line[2]
