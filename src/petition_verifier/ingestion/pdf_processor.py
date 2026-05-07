"""
OCR abstraction layer.

Swap backend by setting OCR_BACKEND env var:
  tesseract  — local Tesseract (default, no API key)
  reducto    — Reducto cloud API (set REDUCTO_API_KEY)
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path

from ..models import ExtractedSignature


class BasePDFProcessor(ABC):
    """Common interface for all OCR backends."""

    @abstractmethod
    def extract(self, pdf_path: Path) -> list[ExtractedSignature]:
        """
        Parse a petition PDF and return one ExtractedSignature per
        signature line found. Page numbers start at 1.
        """
        ...


def get_processor(backend: str | None = None) -> BasePDFProcessor:
    """Factory — returns the configured backend."""
    backend = backend or os.getenv("OCR_BACKEND", "tesseract")

    if backend == "tesseract":
        from .tesseract import TesseractProcessor
        return TesseractProcessor()

    if backend == "reducto":
        from .reducto import ReductoProcessor
        api_key = os.getenv("REDUCTO_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "OCR_BACKEND=reducto but REDUCTO_API_KEY is not set. "
                "Add it to your .env file."
            )
        return ReductoProcessor(api_key=api_key)

    if backend == "vision":
        creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds:
            raise EnvironmentError(
                "OCR_BACKEND=vision but GOOGLE_APPLICATION_CREDENTIALS is not set.\n"
                "Point it to your service account JSON key file in .env."
            )
        from .vision import VisionProcessor
        return VisionProcessor()

    if backend == "claude":
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise EnvironmentError(
                "OCR_BACKEND=claude but ANTHROPIC_API_KEY is not set.\n"
                "Add it to your .env file."
            )
        from .claude_extractor import ClaudeProcessor
        return ClaudeProcessor()

    if backend == "vision_field":
        creds = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if not creds:
            raise EnvironmentError(
                "OCR_BACKEND=vision_field but GOOGLE_APPLICATION_CREDENTIALS is not set."
            )
        from .field_vision import FieldVisionProcessor
        return FieldVisionProcessor()

    raise ValueError(
        f"Unknown OCR_BACKEND: {backend!r}. "
        "Use 'tesseract', 'vision', 'vision_field', 'reducto', or 'claude'."
    )
