from __future__ import annotations

from petition_verifier.models import VerificationStatus
from petition_verifier.verification_policy import (
    classify_signature_status,
    packet_line_bulk_approvable,
)


def test_classify_signature_status_requires_signature_for_auto_approval():
    assert classify_signature_status(99, True) == VerificationStatus.APPROVED
    assert classify_signature_status(99, False) == VerificationStatus.REVIEW


def test_classify_signature_status_handles_review_reject_and_duplicate():
    assert classify_signature_status(72, True) == VerificationStatus.REVIEW
    assert classify_signature_status(20, True) == VerificationStatus.REJECTED
    assert classify_signature_status(99, True, duplicate_of_line=1) == VerificationStatus.DUPLICATE


def test_packet_line_bulk_approvable_requires_clean_valid_signed_new_row():
    base = {
        "row_status": "new_signature",
        "has_signature": True,
        "voter_status": "valid",
        "action": None,
        "ai_verdict": "likely_valid",
        "ai_flags": [],
        "fraud_flags": [],
        "fraud_score": 0,
        "review_decision": None,
    }
    assert packet_line_bulk_approvable(**base) is True

    for override in [
        {"row_status": "already_counted"},
        {"has_signature": False},
        {"voter_status": "uncertain"},
        {"action": "rejected"},
        {"ai_verdict": "likely_invalid"},
        {"ai_flags": ["low_confidence"]},
        {"fraud_flags": ["same_handwriting"]},
        {"fraud_score": 40},
        {"review_decision": "confirmed_fraud"},
    ]:
        candidate = {**base, **override}
        assert packet_line_bulk_approvable(**candidate) is False
