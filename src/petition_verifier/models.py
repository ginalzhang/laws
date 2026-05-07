"""Core data models shared across the pipeline."""
from __future__ import annotations

from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class VerificationStatus(str, Enum):
    APPROVED = "approved"   # confidence >= THRESHOLD_APPROVE
    REVIEW   = "review"     # THRESHOLD_REVIEW <= confidence < THRESHOLD_APPROVE
    REJECTED = "rejected"   # confidence < THRESHOLD_REVIEW
    DUPLICATE = "duplicate" # same voter already appears in this batch


class BoundingBox(BaseModel):
    x: int
    y: int
    width: int
    height: int
    page: int = 1


class ExtractedSignature(BaseModel):
    """Raw output from the OCR layer — one row on a petition sheet."""
    line_number: int
    page: int
    raw_name: str = ""
    raw_address: str = ""
    raw_date: str = ""
    signature_present: bool = False
    signature_bbox: Optional[BoundingBox] = None
    # OCR confidence from Tesseract (0-100); None if not available
    ocr_confidence: Optional[float] = None
    # Grayscale grid-cell density vector of the handwritten content area.
    # Populated by the Vision backend for same-handwriting fraud detection.
    handwriting_vector: Optional[list] = None


class NormalizedSignature(BaseModel):
    """Cleaned fields ready for voter roll lookup."""
    line_number: int
    page: int
    first_name: str = ""
    last_name: str = ""
    street: str = ""
    city: str = ""
    state: str = ""
    zip_code: str = ""
    date: str = ""
    signature_present: bool = False
    signature_bbox: Optional[BoundingBox] = None
    # The combined search key used for fuzzy matching
    search_key: str = ""


class VoterMatch(BaseModel):
    """Result of matching a NormalizedSignature against the voter roll."""
    voter_id: str
    voter_name: str
    voter_address: str
    confidence: float = Field(ge=0, le=100)
    name_score: float = Field(ge=0, le=100)
    address_score: float = Field(ge=0, le=100)


class VerificationResult(BaseModel):
    """Final output record for one signature line."""
    line_number: int
    page: int

    # What was extracted
    extracted: ExtractedSignature
    normalized: NormalizedSignature

    # Match result
    best_match: Optional[VoterMatch] = None
    status: VerificationStatus
    duplicate_of_line: Optional[int] = None

    # Staff correction (set by review UI)
    staff_override: Optional[VerificationStatus] = None
    staff_voter_id: Optional[str] = None
    staff_notes: str = ""

    @property
    def final_status(self) -> VerificationStatus:
        return self.staff_override if self.staff_override else self.status

    @property
    def confidence(self) -> float:
        return self.best_match.confidence if self.best_match else 0.0


class ProjectResult(BaseModel):
    """Full output for one petition PDF."""
    project_id: str
    pdf_path: str
    total_lines: int
    approved: int = 0
    review: int = 0
    rejected: int = 0
    duplicates: int = 0
    signatures: list[VerificationResult] = []

    def summary(self) -> dict:
        return {
            "project_id": self.project_id,
            "pdf_path": self.pdf_path,
            "total": self.total_lines,
            "approved": self.approved,
            "review": self.review,
            "rejected": self.rejected,
            "duplicates": self.duplicates,
            "auto_rate_pct": round(self.approved / max(self.total_lines, 1) * 100, 1),
        }
