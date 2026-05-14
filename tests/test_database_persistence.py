from __future__ import annotations

from petition_verifier.models import (
    ExtractedSignature,
    NormalizedSignature,
    ProjectResult,
    VerificationResult,
    VerificationStatus,
)
from petition_verifier.storage.database import Database, SignatureRow


def _result(project_id: str, raw_name: str) -> ProjectResult:
    extracted = ExtractedSignature(
        line_number=1,
        page=1,
        raw_name=raw_name,
        raw_address="123 Main St",
        signature_present=True,
    )
    normalized = NormalizedSignature(
        line_number=1,
        page=1,
        first_name=raw_name.split()[0].lower(),
        last_name=raw_name.split()[-1].lower(),
        street="123 main st",
        search_key=raw_name.lower(),
        signature_present=True,
    )
    verification = VerificationResult(
        line_number=1,
        page=1,
        extracted=extracted,
        normalized=normalized,
        status=VerificationStatus.REVIEW,
    )
    return ProjectResult(
        project_id=project_id,
        pdf_path="packet.pdf",
        total_lines=1,
        review=1,
        signatures=[verification],
    )


def test_save_project_replaces_existing_signature_rows_for_same_project(tmp_path):
    db = Database(f"sqlite:///{tmp_path / 'petition.db'}")

    db.save_project(_result("project-1", "Jane Smith"))
    db.save_project(_result("project-1", "Jane Smith Updated"))

    with db._Session() as session:
        rows = session.query(SignatureRow).filter_by(project_id="project-1").all()

    assert len(rows) == 1
    assert rows[0].raw_name == "Jane Smith Updated"
