"""
Claude Vision extraction backend.

Sends each page image directly to Claude and asks for structured extraction of
all signer rows — one API call per page, no coordinate math required.

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
This is a California petition signature sheet with 7-8 numbered rows of handwritten voter information. Each row has a printed row number (1, 2, 3...) on the left side.

For each numbered row, extract the handwritten text only — not the pre-printed field labels like "Print Name", "Address Only", "City", "Zip". The handwritten content you want is: the person's name written on the Print Name line, their street address, their city, and their zip code.

Also note for each row whether a handwritten signature is present in the Signature area, and any other row numbers whose handwriting looks visually identical to this row (a fraud signal).

If a row appears blank or unsigned, skip it.

Return results as a JSON array — no markdown fences, no commentary — with one object per non-blank row:
[{"row_number": 1, "name": "Jane Smith", "address": "123 Main St", "city": "Los Angeles", "zip": "90001", "has_signature": true, "same_handwriting_as": []}, ...]

If no rows have handwritten content, return: []"""


_API_IMAGE_MAX_DIM = 1600   # Anthropic-recommended ceiling; keeps payload small
_API_IMAGE_QUALITY = 85     # plenty for handwriting at this resolution


def _to_base64_jpeg(image: Image.Image) -> str:
    """Encode image as base64 JPEG, resized so the long edge fits within
    _API_IMAGE_MAX_DIM. Petition photos arrive at 3024x4032 (~12 megapixels)
    which encodes to ~10MB of base64 and has been timing out at the network
    layer on Render. Resizing to 1600px long edge brings the payload to
    ~150-300KB while still leaving rows ~80px tall — readable by the model."""
    if max(image.size) > _API_IMAGE_MAX_DIM:
        scaled = image.copy()
        scaled.thumbnail((_API_IMAGE_MAX_DIM, _API_IMAGE_MAX_DIM), Image.LANCZOS)
    else:
        scaled = image
    buf = io.BytesIO()
    scaled.save(buf, format="JPEG", quality=_API_IMAGE_QUALITY)
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

        client = anthropic.Anthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            timeout=60.0,
        )
        all_sigs: list[ExtractedSignature] = []
        line_counter = 1

        for page_num, image in enumerate(images, start=1):
            print(
                f"[claude_extractor] page {page_num}: sending {image.width}x{image.height}px image to API",
                flush=True,
            )
            rows = self._call_claude(client, image, page_num)
            page_sigs = self._rows_to_sigs(rows, page_num, line_counter)
            line_counter += len(page_sigs)
            all_sigs.extend(page_sigs)

        return all_sigs

    def _call_claude(self, client, image: Image.Image, page_num: int = 1) -> list[dict]:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
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
        raw = response.content[0].text.strip()
        # Log the raw response so we can see what the model actually returned
        # before any parsing — useful for diagnosing blank-row symptoms
        # (refusal, empty array, malformed JSON, etc).
        print(f"[claude_extractor] page {page_num} raw response:\n{raw}\n", flush=True)

        # Strip markdown fences if model adds them despite instructions
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Claude returned non-JSON: {raw[:300]}") from exc

        # New prompt asks for a bare list. Older responses or alternate prompts
        # may wrap it as {"rows": [...]} — handle both.
        if isinstance(parsed, dict) and "rows" in parsed:
            rows = parsed["rows"]
        elif isinstance(parsed, list):
            rows = parsed
        else:
            rows = []

        return [r for r in rows if r is not None]

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
            # has_signature may be omitted by the model; default to True if any
            # other content exists, False only on truly empty rows.
            has_sig = bool(row.get("has_signature", bool(name or address)))

            if not name and not address:
                continue

            full_address = ", ".join(filter(None, [address, city, zip_]))

            # Accept any of the three keys the model has been asked to use across
            # prompt versions: row_number (current), row, line (legacy).
            raw_line = row.get("row_number") or row.get("row") or row.get("line")
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
                raw_date="",
                signature_present=has_sig,
            ))
        return sigs
