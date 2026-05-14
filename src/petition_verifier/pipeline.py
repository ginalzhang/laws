"""
Main pipeline: PDF → extract → normalize → match → detect dupes → result.

    from petition_verifier.pipeline import Pipeline

    pipeline = Pipeline(voter_roll_csv="voter_roll.csv")
    result = pipeline.process("petition.pdf", project_id="project-001")
    print(result.model_dump_json(indent=2))
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from .ingestion import get_processor
from .matching import DuplicateDetector, VoterMatcher, normalize_signature
from .models import ProjectResult, VerificationResult, VerificationStatus
from .verification_policy import classify_signature_status

THRESHOLD_APPROVE = int(os.getenv("THRESHOLD_APPROVE", "85"))
THRESHOLD_REVIEW  = int(os.getenv("THRESHOLD_REVIEW",  "70"))

class Pipeline:
    def __init__(
        self,
        voter_roll_csv: str | Path | None = None,
        ocr_backend: str | None = None,
    ):
        self._processor = get_processor(ocr_backend)
        self._matcher   = VoterMatcher.from_csv(voter_roll_csv) if voter_roll_csv else None

    def process(
        self,
        pdf_path: str | Path,
        project_id: str | None = None,
    ) -> ProjectResult:
        pdf_path   = Path(pdf_path)
        project_id = project_id or str(uuid.uuid4())[:8]
        detector   = DuplicateDetector()

        # 1. OCR
        extracted = self._processor.extract(pdf_path)

        # 2. Normalize + match + dedup
        results: list[VerificationResult] = []

        for ext in extracted:
            norm  = normalize_signature(ext)
            match = self._matcher.match(norm) if self._matcher else None

            dupe_of = detector.check(norm)
            confidence = match.confidence if match else 0.0
            status = classify_signature_status(
                confidence=confidence,
                signature_present=ext.signature_present,
                duplicate_of_line=dupe_of,
                threshold_approve=THRESHOLD_APPROVE,
                threshold_review=THRESHOLD_REVIEW,
            )

            results.append(
                VerificationResult(
                    line_number=ext.line_number,
                    page=ext.page,
                    extracted=ext,
                    normalized=norm,
                    best_match=match,
                    status=status,
                    duplicate_of_line=dupe_of,
                )
            )

        # 3. Aggregate counts
        pr = ProjectResult(
            project_id=project_id,
            pdf_path=str(pdf_path),
            total_lines=len(results),
            approved=sum(1 for r in results if r.status == VerificationStatus.APPROVED),
            review=sum(1 for r in results if r.status == VerificationStatus.REVIEW),
            rejected=sum(1 for r in results if r.status == VerificationStatus.REJECTED),
            duplicates=sum(1 for r in results if r.status == VerificationStatus.DUPLICATE),
            signatures=results,
        )

        return pr
