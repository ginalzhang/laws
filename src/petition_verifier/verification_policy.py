from __future__ import annotations

from .models import VerificationStatus


def classify_signature_status(
    confidence: float,
    signature_present: bool,
    duplicate_of_line: int | None = None,
    threshold_approve: int = 85,
    threshold_review: int = 70,
) -> VerificationStatus:
    """Classify one classic pipeline signature row.

    Auto-approval requires both a strong voter match and a detected signature.
    A strong voter match with a missing signature stays in staff review.
    """
    if duplicate_of_line is not None:
        return VerificationStatus.DUPLICATE
    if confidence >= threshold_approve:
        return VerificationStatus.APPROVED if signature_present else VerificationStatus.REVIEW
    if confidence >= threshold_review:
        return VerificationStatus.REVIEW
    return VerificationStatus.REJECTED


def packet_line_bulk_approvable(
    *,
    row_status: str,
    has_signature: bool,
    voter_status: str | None,
    action: str | None = None,
    ai_verdict: str | None = None,
    ai_flags: list | None = None,
    low_confidence_fields: list | None = None,
    fraud_flags: list | None = None,
    fraud_score: int | None = None,
    review_decision: str | None = None,
) -> bool:
    """Return whether approve-all may safely approve a review packet row."""
    return (
        row_status == "new_signature"
        and not action
        and has_signature
        and ai_verdict != "likely_invalid"
        and voter_status == "valid"
        and not (ai_flags or [])
        and not (low_confidence_fields or [])
        and not (fraud_flags or [])
        and (fraud_score or 0) == 0
        and review_decision != "confirmed_fraud"
    )
