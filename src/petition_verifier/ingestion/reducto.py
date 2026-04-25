"""
Reducto cloud OCR backend.

Swap in by setting OCR_BACKEND=reducto and REDUCTO_API_KEY=<key> in .env.
Reducto handles complex scanned documents better than local Tesseract —
especially useful for multi-column petition sheets with varying layouts.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

from ..models import BoundingBox, ExtractedSignature
from .pdf_processor import BasePDFProcessor

REDUCTO_PARSE_URL = "https://platform.reducto.ai/parse"

# Schema we ask Reducto to extract per signature row
EXTRACTION_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "name":               {"type": "string"},
            "address":            {"type": "string"},
            "date":               {"type": "string"},
            "signature_present":  {"type": "boolean"},
        },
        "required": ["name", "address"],
    },
}


class ReductoProcessor(BasePDFProcessor):
    def __init__(self, api_key: str):
        self._api_key = api_key

    def extract(self, pdf_path: Path) -> list[ExtractedSignature]:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)

        with open(pdf_path, "rb") as f:
            response = requests.post(
                REDUCTO_PARSE_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                files={"file": (pdf_path.name, f, "application/pdf")},
                data={
                    "extraction_schema": json.dumps(EXTRACTION_SCHEMA),
                    "options": json.dumps({"extract_images": True}),
                },
                timeout=120,
            )

        response.raise_for_status()
        payload = response.json()

        sigs: list[ExtractedSignature] = []
        line_counter = 1

        # Reducto returns pages → chunks → extracted fields
        for page_data in payload.get("pages", []):
            page_num = page_data.get("page_number", 1)
            for item in page_data.get("extracted", []):
                bbox_data = item.get("bounding_box")
                sig_bbox = None
                if bbox_data:
                    sig_bbox = BoundingBox(
                        x=bbox_data.get("x", 0),
                        y=bbox_data.get("y", 0),
                        width=bbox_data.get("width", 0),
                        height=bbox_data.get("height", 0),
                        page=page_num,
                    )

                sigs.append(
                    ExtractedSignature(
                        line_number=line_counter,
                        page=page_num,
                        raw_name=item.get("name", ""),
                        raw_address=item.get("address", ""),
                        raw_date=item.get("date", ""),
                        signature_present=item.get("signature_present", False),
                        signature_bbox=sig_bbox,
                    )
                )
                line_counter += 1

        return sigs
