"""
Claude Vision extraction backend.

Sends each page image directly to Claude and asks for structured extraction of
all signer rows plus handwriting-similarity assessment — one API call per page,
no coordinate math required.

Setup:
  Add to .env:
    OCR_BACKEND=claude
    ANTHROPIC_API_KEY=<your key>

Model: claude-haiku-4-5-20251001 (fast, cheap, excellent at structured forms)
Cost: ~$0.002 per petition page
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
from pathlib import Path

import pillow_heif
from PIL import Image

from ..models import ExtractedSignature
from .pdf_processor import BasePDFProcessor
from .tesseract import DPI, IMAGE_SUFFIXES


_PROMPT = """\
This is a California initiative petition signature sheet photographed with a phone camera.

Layout of the page:
• Large printed header at the top (ballot title, Official Top Funders box, Notice to Public) — IGNORE.
• Numbered signer rows 1–7 in the middle. Each row spans TWO physical lines:
    Top line:    [row#]  Print Name: ___________  Residence Address ONLY: ___________
    Bottom line:          Signature: ___________  City: ___________  Zip: ___________
• "Declaration of Circulator" section at the bottom — IGNORE.

For each FILLED row (skip completely blank rows) extract:
  line            — the printed row number (integer 1–7)
  name            — handwritten text written after the "Print Name:" label
  address         — handwritten street address after "Residence Address ONLY:" (number + street, no city/zip)
  city            — handwritten text after "City:" label
  zip             — handwritten 5-digit zip code (digits only, e.g. "90001")
  has_signature   — true if there is handwritten ink in the Signature area, false if blank
  same_handwriting_as — list of OTHER row numbers whose handwriting looks identical (fraud signal). Use [].

Rules:
• Extract ONLY handwritten content — ignore all printed labels and instructions.
• Use "" for any field that is blank or unreadable.
• A colored sticker covering the county field is normal — ignore it.

Return ONLY valid JSON — no markdown fences, no explanation:
[{"line":1,"name":"Jane Smith","address":"123 Main St","city":"Los Angeles","zip":"90001","has_signature":true,"same_handwriting_as":[]},...]

If nothing is filled in, return: []"""


def _to_base64_jpeg(image: Image.Image) -> str:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=92)
    return base64.standard_b64encode(buf.getvalue()).decode()


class ClaudeProcessor(BasePDFProcessor):
    """
    Claude Vision OCR backend.

    Handles tilted, low-light, and cropped petition photos that break
    coordinate-based extraction. Claude understands the form structure
    and returns clean structured data directly.
    """

    def extract(self, pdf_path: Path) -> list[ExtractedSignature]:
        import anthropic

        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)

        suffix = pdf_path.suffix.lower()
        if suffix == ".pdf":
            from pdf2image import convert_from_path
            images = convert_from_path(str(pdf_path), dpi=DPI)
        elif suffix in IMAGE_SUFFIXES:
            pillow_heif.register_heif_opener()
            images = [Image.open(pdf_path).convert("RGB")]
        else:
            raise ValueError(f"Unsupported file type: {suffix}")

        client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        all_sigs: list[ExtractedSignature] = []
        line_counter = 1

        for page_num, image in enumerate(images, start=1):
            rows = self._call_claude(client, image)
            page_sigs = self._rows_to_sigs(rows, page_num, line_counter)
            line_counter += len(page_sigs)
            all_sigs.extend(page_sigs)

        return all_sigs

    def _call_claude(self, client, image: Image.Image) -> list[dict]:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": _to_base64_jpeg(image),
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }],
        )
        text = response.content[0].text.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)

    def _rows_to_sigs(
        self,
        rows: list[dict],
        page_num: int,
        line_start: int,
    ) -> list[ExtractedSignature]:
        sigs: list[ExtractedSignature] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            name    = str(row.get("name",    "")).strip()
            address = str(row.get("address", "")).strip()
            city    = str(row.get("city",    "")).strip()
            zip_    = str(row.get("zip",     "")).strip()
            date    = str(row.get("date",    "")).strip()
            has_sig = bool(row.get("has_signature", False))

            # Convert same_handwriting_as from form row numbers to absolute line numbers
            raw_same = row.get("same_handwriting_as", [])
            same_hw: list[int] = []
            for x in raw_same:
                try:
                    n = int(x)
                    if 1 <= n <= 8:
                        same_hw.append(line_start + n - 1)
                except (TypeError, ValueError):
                    pass

            if not name and not address:
                continue

            full_address = ", ".join(filter(None, [address, city, zip_]))

            raw_line = row.get("line")
            try:
                raw_line = int(raw_line)
                line_num = line_start + raw_line - 1 if 1 <= raw_line <= 8 else line_start + len(sigs)
            except (TypeError, ValueError):
                line_num = line_start + len(sigs)

            sigs.append(ExtractedSignature(
                line_number=line_num,
                page=page_num,
                raw_name=name,
                raw_address=full_address,
                raw_date=date,
                signature_present=has_sig,
                same_handwriting_as=same_hw or None,
            ))
        return sigs
