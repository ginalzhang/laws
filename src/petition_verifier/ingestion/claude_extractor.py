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
You are extracting handwritten voter information from a California petition signature sheet.

CRITICAL: The petition has TWO types of text:
1. PRINTED text — crisp, uniform, pre-printed form labels and legal language. IGNORE ALL OF THIS.
2. HANDWRITTEN text — messy, irregular, written by hand. THIS IS WHAT YOU EXTRACT.

The form has numbered rows 1 through 7 (or 8). Each row number appears on the left edge. Inside each row:
- The person's HANDWRITTEN name appears on the "Print Name" line
- Their HANDWRITTEN street address appears on the "Residence Address Only" line
- Their HANDWRITTEN city appears after the "City:" label
- Their HANDWRITTEN zip code appears after the "Zip:" label

For each row, return the handwritten values. If a field is hard to read, give your best guess — never skip it. If a row is genuinely empty (no handwriting at all), return null for that row.

Return ONLY this JSON, no other text:
{
  "rows": [
    {"row": 1, "name": "...", "address": "...", "city": "...", "zip": "..."},
    {"row": 2, "name": "...", "address": "...", "city": "...", "zip": "..."},
    ...
  ]
}"""

# Words that should never appear in handwritten voter fields — if they do, the
# model is reading printed boilerplate instead of handwritten voter data.
_BOILERPLATE_RE = re.compile(
    r"\b(PROPONENTS?|SIGNERS?|ATTORNEY|INITIATIVE|PETITION|CIRCULATOR|"
    r"QUALIFIED|REGISTERED|SECRETARY|PROPOSITION|MEASURE|PURSUANT|"
    r"SECTION|PARAGRAPH|FISCAL|IMPACT|ORDINANCE|STATUTE|GOVERNMENT|"
    r"WHEREAS|CERTIFY|DECLARATION|SPONSOR|ASSEMBLY|SENATE|DISTRICT|"
    r"HEREBY|OFFICIAL|FUNDERS?|NOTICE|BALLOT|PROPONENT)\b",
    re.IGNORECASE,
)


def _to_base64_jpeg(image: Image.Image) -> str:
    """Encode image as base64 JPEG at full resolution (no downscaling)."""
    buf = io.BytesIO()
    # quality=95 preserves fine handwriting detail; no resize so full resolution is sent
    image.save(buf, format="JPEG", quality=95)
    return base64.standard_b64encode(buf.getvalue()).decode()


def _is_boilerplate(text: str) -> bool:
    return bool(text and _BOILERPLATE_RE.search(text))


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
        # Log raw response so we can see exactly what the model returns
        print(f"[claude_extractor] page {page_num} raw response:\n{raw}\n", flush=True)

        # Strip markdown fences if model adds them despite instructions
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Claude returned non-JSON: {raw[:300]}") from exc

        # New format: {"rows": [...]}  — fall back to bare list for old responses
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
            # has_signature not in new prompt format — infer from presence of content
            has_sig = bool(row.get("has_signature", bool(name or address)))

            # Detect boilerplate leakage
            if _is_boilerplate(name) or _is_boilerplate(address):
                print(
                    f"[claude_extractor] row {row.get('row', '?')} flagged as extraction_error "
                    f"(boilerplate detected): name={name!r} address={address!r}",
                    flush=True,
                )
                raw_line = row.get("row") or row.get("line")
                try:
                    line_num = line_start + int(raw_line) - 1
                except (TypeError, ValueError):
                    line_num = line_start + len(sigs)
                sigs.append(ExtractedSignature(
                    line_number=line_num,
                    page=page_num,
                    raw_name="[EXTRACTION_ERROR: boilerplate]",
                    raw_address="",
                    raw_date="",
                    signature_present=False,
                ))
                continue

            if not name and not address:
                continue

            full_address = ", ".join(filter(None, [address, city, zip_]))

            # New format uses "row" key; old format used "line"
            raw_line = row.get("row") or row.get("line")
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
