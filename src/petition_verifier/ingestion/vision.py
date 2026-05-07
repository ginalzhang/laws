"""
Google Cloud Vision OCR backend.

Much better than Tesseract for handwritten petition forms.
Uses document_text_detection which handles cursive and mixed print/handwriting.

Setup (one-time):
  1. https://console.cloud.google.com → create/select a project
  2. APIs & Services → Enable "Cloud Vision API"
  3. IAM & Admin → Service Accounts → Create → download JSON key
  4. Add to .env:
       OCR_BACKEND=vision
       GOOGLE_APPLICATION_CREDENTIALS=/path/to/your-key.json

Free tier: 1,000 pages/month. After that: ~$1.50 per 1,000 pages.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Optional

import pillow_heif
from PIL import Image

from ..models import BoundingBox, ExtractedSignature
from .pdf_processor import BasePDFProcessor
from .tesseract import IMAGE_SUFFIXES, DPI, _Word

# ── Printed label patterns (match Vision's word tokens) ──────────────────────
_LABEL_NAME    = re.compile(r"^print$|^name$|^name:$", re.I)
_LABEL_ADDR    = re.compile(r"^residence$|^address$|^address:$|^only$|^only:$", re.I)
_LABEL_CITY    = re.compile(r"^city$|^city:$", re.I)
_LABEL_STATE   = re.compile(r"^state$|^state:$|^ca$", re.I)
_LABEL_ZIP     = re.compile(r"^zip$|^zip:$", re.I)
_LABEL_DATE    = re.compile(r"^date$|^date:$", re.I)
_LABEL_SIG     = re.compile(r"^signature$|^signature:$", re.I)
_LINE_NUM      = re.compile(r"^\d{1,2}\.?$")   # "1", "2.", "1.", etc.

# Vertical window around a "Print Name" anchor to collect a signer's words.
# On CA initiative petitions the "Residence Address" row sits ~50px ABOVE
# the "Print Name" row, so we need a generous above-window.
_BLOCK_ABOVE_PX = 70
_BLOCK_BELOW_PX = 200

# How close two words must be (y) to be "on the same row"
_ROW_MERGE_PX = 35


def _load_images_pil(path: Path) -> list[Image.Image]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        from pdf2image import convert_from_path
        return convert_from_path(str(path), dpi=DPI)
    if suffix in IMAGE_SUFFIXES:
        pillow_heif.register_heif_opener()
        return [Image.open(path).convert("RGB")]
    raise ValueError(f"Unsupported file type: {suffix}")


def _pil_to_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _make_vision_client():
    """Build a Vision client, supporting JSON content in env var for Render/cloud deploys."""
    import os, json
    from google.cloud import vision as gv
    from google.oauth2 import service_account

    creds_env = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
    # If the env var looks like JSON content (not a file path), load it directly
    if creds_env.strip().startswith("{"):
        info = json.loads(creds_env)
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        return gv.ImageAnnotatorClient(credentials=credentials)
    # Otherwise fall back to default (file path or ADC)
    return gv.ImageAnnotatorClient()


def _vision_words(image: Image.Image) -> list[_Word]:
    """Call Cloud Vision and return words with bounding-box coords."""
    from google.cloud import vision as gv

    client   = _make_vision_client()
    response = client.document_text_detection(image=gv.Image(content=_pil_to_bytes(image)))

    if response.error.message:
        raise RuntimeError(
            f"Cloud Vision API error: {response.error.message}\n"
            "Check your credentials and that the Vision API is enabled."
        )

    words: list[_Word] = []
    for page in response.full_text_annotation.pages:
        for block in page.blocks:
            for para in block.paragraphs:
                for word in para.words:
                    text = "".join(s.text for s in word.symbols).strip()
                    if not text:
                        continue
                    verts  = word.bounding_box.vertices
                    xs     = [v.x for v in verts]
                    ys     = [v.y for v in verts]
                    words.append(_Word(
                        text=text,
                        conf=word.confidence * 100,
                        left=min(xs),
                        top=min(ys),
                        width=max(xs) - min(xs),
                        height=max(ys) - min(ys),
                    ))
    return words


# ── Block-format detection ────────────────────────────────────────────────────

def _find_print_name_anchors(words: list[_Word]) -> list[_Word]:
    """
    Find all "Print Name" label positions.
    Handles Vision returning "Print" and "Name" as separate words
    at slightly different y-coordinates (common OCR behaviour).
    Returns the _Word for the "Print" token of each pair found.
    """
    anchors: list[_Word] = []
    for w in words:
        if not re.match(r"^print$", w.text, re.I):
            continue
        # Look for a nearby "Name" or "Name:" word anywhere in the word list —
        # within ±40px vertically and 0–300px to the right
        has_name = any(
            re.match(r"^name:?$", other.text, re.I)
            and abs(other.top - w.top) <= 40
            and 0 < (other.left - w.left) < 300
            for other in words
        )
        if has_name:
            anchors.append(w)

    # Sort by vertical position so line numbers come out in order
    return sorted(anchors, key=lambda w: w.top)


def _is_vision_block_format(words: list[_Word]) -> bool:
    return len(_find_print_name_anchors(words)) >= 2


# ── Word classification helpers ───────────────────────────────────────────────

_ALL_LABELS = re.compile(
    r"^(print|name|residence|address|only|city|state|zip|date|signature|"
    r"res|addr|county|ca|page|"
    r"circulator|declaration|circulated|circulating|circulation)$",
    re.I
)

# Tokens that are pure punctuation / noise — never signer content
_NOISE = re.compile(r"^[:\-_\.\,\;\(\)\/\|]{1,3}$")

def _is_printed_label(text: str) -> bool:
    """Return True if this word is a printed form label or punctuation noise."""
    clean = text.strip(":.,_- ").strip()
    if not clean:
        return True
    if _NOISE.match(text):
        return True
    return bool(_ALL_LABELS.match(clean))


def _words_right_of(
    anchor: _Word,
    all_words: list[_Word],
    y_tol: int = _ROW_MERGE_PX,
    min_x_gap: int = 10,
    max_x: Optional[int] = None,
) -> list[_Word]:
    """Words to the right of anchor on roughly the same row, excluding labels."""
    result = [
        w for w in all_words
        if abs(w.top - anchor.top) <= y_tol
        and w.left > anchor.right + min_x_gap
        and not _is_printed_label(w.text)
        and (max_x is None or w.left < max_x)
    ]
    return sorted(result, key=lambda w: w.left)


def _words_in_region(
    all_words: list[_Word],
    y_min: int,
    y_max: int,
    x_min: int = 0,
    x_max: int = 99999,
    exclude_labels: bool = True,
) -> list[_Word]:
    return [
        w for w in all_words
        if y_min <= w.top <= y_max
        and x_min <= w.left <= x_max
        and (not exclude_labels or not _is_printed_label(w.text))
    ]


def _join(words: list[_Word], merge_gap: int = 15) -> str:
    """
    Join words left-to-right.  Words whose horizontal gap is <= merge_gap px
    are fused without a space — this fixes Vision splitting a single handwritten
    token (e.g. "Leo" + "or" → "Leonor", "Tor" + "kelson" → "Torkelson").
    """
    if not words:
        return ""
    ordered = sorted(words, key=lambda w: w.left)
    parts: list[str] = [ordered[0].text]
    for prev, cur in zip(ordered, ordered[1:]):
        gap = cur.left - prev.right
        parts.append("" if gap <= merge_gap else " ")
        parts.append(cur.text)
    return "".join(parts).strip()


# ── Main block-format extractor ───────────────────────────────────────────────

def _extract_vision_block(
    words: list[_Word],
    image: Image.Image,
    page_num: int,
    line_start: int,
) -> list[ExtractedSignature]:
    """
    Parse a California initiative petition page (block format).

    Strategy:
      1. Find every "Print Name:" label pair as a signer anchor.
      2. Collect all words in a vertical window around each anchor.
      3. Within that window, split by x-position:
           - Words right of the Name label → raw_name
           - Words right of any Residence/Address label → raw_address
           - Words right of City label → city (appended to address)
           - Words right of Date label → raw_date
      4. Detect signature presence: any non-label word to the LEFT of the
         Print Name label within the block window.
    """
    page_width = image.width
    anchors    = _find_print_name_anchors(words)
    sigs: list[ExtractedSignature] = []

    # Ignore everything above the signature grid.  The preamble/header text
    # can contain "Print" + "Name" coincidences that look like anchors and
    # produce spurious signer lines.  Use a 200px buffer above the topmost
    # real anchor so we don't accidentally clip the first signer's block.
    if anchors:
        grid_top = anchors[0].top - 200
        words = [w for w in words if w.top >= grid_top]
        # Re-derive anchors from the filtered word list so the indexes stay consistent.
        anchors = _find_print_name_anchors(words)

    # Find any "DECLARATION" marker to use as a hard stop
    declaration_top = next(
        (w.top for w in words if re.match(r"^declaration$", w.text, re.I)),
        None,
    )

    sig_num = 0  # count of actual signer lines emitted
    for idx, anchor in enumerate(anchors):
        # ── Skip circulator / preamble anchors ────────────────────────────────
        # Real signer rows always have a line-number digit (1–7) printed to the
        # left of the anchor, in a tight horizontal band.  Header text and the
        # circulator block never have such a digit.
        has_line_number = any(
            re.match(r"^[1-7]$", w.text)
            and w.left < anchor.left
            and w.left > anchor.left - 200
            and abs(w.top - anchor.top) <= 60
            for w in words
        )
        if not has_line_number:
            continue

        # Vertical window for this signer's block.
        # Cap the bottom at: next anchor top, declaration marker, or default window.
        next_anchor_top = anchors[idx + 1].top if idx + 1 < len(anchors) else anchor.top + _BLOCK_BELOW_PX
        hard_stop = min(
            next_anchor_top - 10,
            (declaration_top - 10) if declaration_top else anchor.top + _BLOCK_BELOW_PX,
        )
        block_top    = anchor.top - _BLOCK_ABOVE_PX
        block_bottom = min(anchor.top + _BLOCK_BELOW_PX, hard_stop)
        block_words  = [w for w in words if block_top <= w.top <= block_bottom]

        # Dense blocks are header/preamble text, not signer rows.
        # Real signer blocks have only a handful of handwritten tokens;
        # reject anything with more than 20 non-label words.
        non_label_count = sum(1 for w in block_words if not _is_printed_label(w.text))
        if non_label_count > 20:
            continue

        zip_pattern = re.compile(r"^\d{5}(-\d{4})?$")

        # ── Name ──────────────────────────────────────────────────────────────
        # Find the "Name:" token (the one right after "Print")
        name_label = next(
            (w for w in sorted(block_words, key=lambda w: w.left)
             if re.match(r"^name:?$", w.text, re.I)
             and abs(w.top - anchor.top) <= _ROW_MERGE_PX
             and w.left > anchor.left),
            None,
        )
        name_anchor = name_label or anchor
        # Name content: right of "Name:" label, left of the address region.
        addr_region_start = anchor.left + int(page_width * 0.25)
        name_words = _words_right_of(name_anchor, block_words,
                                     y_tol=_ROW_MERGE_PX,
                                     max_x=addr_region_start)
        # Fallback: look just below the anchor row (tight window)
        if not name_words:
            below_row_words = _words_in_region(
                block_words,
                y_min=anchor.bottom,
                y_max=anchor.bottom + 30,
                x_min=anchor.left,
                x_max=addr_region_start,
            )
            name_words = sorted(below_row_words, key=lambda w: w.left)
        raw_name = _join(name_words)

        # ── Address ───────────────────────────────────────────────────────────
        addr_label = next(
            (w for w in sorted(block_words, key=lambda w: (w.top, w.left))
             if re.match(r"^(residence|address:?)$", w.text, re.I)
             and w.left > addr_region_start - 200),
            None,
        )
        street_text = ""
        if addr_label:
            # Use the "ONLY" label as the right boundary of the street prefix.
            only_label = next(
                (w for w in block_words
                 if re.match(r"^only:?$", w.text, re.I)
                 and abs(w.top - addr_label.top) <= _ROW_MERGE_PX),
                addr_label,
            )
            # Handwriting drifts above the printed label — use asymmetric window.
            street_words = _words_in_region(
                block_words,
                y_min=only_label.top - 50,
                y_max=only_label.top + 15,
                x_min=only_label.right + 5,
            )
            # Exclude 5-digit zip codes from the street field
            street_words = [w for w in street_words if not zip_pattern.match(w.text)]
            street_text = _join(street_words)

        # Zip — extract first so we can exclude it from city
        zip_label = next(
            (w for w in block_words if re.match(r"^zip:?$", w.text, re.I)),
            None,
        )
        zip_text = ""
        if zip_label:
            zip_words = _words_right_of(zip_label, block_words, y_tol=_ROW_MERGE_PX)
            # Only keep actual 5-digit zip codes — avoid grabbing nearby form text
            zip_words = [w for w in zip_words if zip_pattern.match(w.text)]
            zip_text = _join(zip_words)

        city_label = next(
            (w for w in block_words if re.match(r"^city:?$", w.text, re.I)),
            None,
        )
        city_text = ""
        if city_label:
            # Asymmetric y window + cap x at Zip label to avoid circulator text.
            city_max_x = zip_label.left - 10 if zip_label else None
            city_words = _words_in_region(
                block_words,
                y_min=city_label.top - 35,
                y_max=city_label.top + 10,
                x_min=city_label.right + 5,
                x_max=city_max_x or 99999,
            )
            city_words = [w for w in city_words
                          if not re.match(r"^(state:?|zip:?|ca)$", w.text, re.I)
                          and not zip_pattern.match(w.text)]
            city_text = _join(city_words)

        raw_address = ", ".join(filter(None, [street_text, city_text, zip_text]))

        # ── Date ──────────────────────────────────────────────────────────────
        date_label = next(
            (w for w in block_words if re.match(r"^date:?$", w.text, re.I)),
            None,
        )
        raw_date = ""
        if date_label:
            # Use asymmetric window — handwriting above the label line
            date_words = _words_in_region(
                block_words,
                y_min=date_label.top - 40,
                y_max=date_label.top + 10,
                x_min=date_label.right + 5,
            )
            raw_date = _join(date_words)

        # ── Signature presence ─────────────────────────────────────────────────
        # Look for non-label OCR tokens right of "Signature:" but within a
        # bounded x range — circulator instruction text lives further right and
        # must not be mistaken for a signer's ink.
        sig_label_word = next(
            (w for w in block_words if re.match(r"^signature:?$", w.text, re.I)),
            None,
        )
        sig_present = False
        sig_bbox    = None
        if sig_label_word:
            # Cap x at sig_label.left + 600 to exclude circulator margin text.
            sig_max_x = sig_label_word.left + 600
            sig_content = _words_right_of(sig_label_word, block_words,
                                          y_tol=50, min_x_gap=5,
                                          max_x=sig_max_x)
            # Fallback: look just below the signature label row
            if not sig_content:
                sig_content = _words_in_region(
                    block_words,
                    y_min=sig_label_word.bottom,
                    y_max=sig_label_word.bottom + 60,
                    x_min=sig_label_word.left,
                    x_max=sig_max_x,
                )
            sig_present = len(sig_content) > 0
            if sig_present:
                sig_bbox = BoundingBox(
                    x=sig_label_word.left,
                    y=sig_label_word.top,
                    width=sig_max_x - sig_label_word.left,
                    height=80,
                    page=page_num,
                )

        avg_conf = (sum(w.conf for w in block_words) / max(len(block_words), 1))

        sigs.append(ExtractedSignature(
            line_number=line_start + sig_num,
            page=page_num,
            raw_name=raw_name,
            raw_address=raw_address,
            raw_date=raw_date,
            signature_present=sig_present,
            signature_bbox=sig_bbox,
            ocr_confidence=round(avg_conf, 1),
        ))
        sig_num += 1

    return sigs


# ── Column-format fallback ────────────────────────────────────────────────────

def _extract_vision_columns(
    words: list[_Word],
    image: Image.Image,
    page_num: int,
    line_counter: int,
) -> list[ExtractedSignature]:
    """
    Fallback for column-format petitions.
    Groups words into rows (generous merge window) then splits by x-bands.
    """
    from .tesseract import (
        _cluster_rows, _detect_column_bands, _is_header_row, _sig_present,
        ROW_MERGE_PX,
    )
    import re as _re

    # Use a wider merge window for Vision output
    orig = ROW_MERGE_PX
    import petition_verifier.ingestion.tesseract as _t
    _t.ROW_MERGE_PX = 40  # patch for Vision's sparser layout

    rows  = _cluster_rows(words)
    bands = _detect_column_bands(rows, image.width)
    sigs: list[ExtractedSignature] = []

    for row in rows:
        if _is_header_row(row):
            continue
        page_width   = image.width
        name_text    = row.text_in_band(bands["name"],    page_width)
        address_text = row.text_in_band(bands["address"], page_width)
        date_text    = row.text_in_band(bands["date"],    page_width)
        sig_present, sig_bbox = _sig_present(row, bands["sig"], page_width)
        if sig_bbox:
            sig_bbox.page = page_num
        if not name_text and not address_text:
            continue
        avg_conf = sum(w.conf for w in row.words) / len(row.words)
        sigs.append(ExtractedSignature(
            line_number=line_counter,
            page=page_num,
            raw_name=name_text,
            raw_address=address_text,
            raw_date=date_text,
            signature_present=sig_present,
            signature_bbox=sig_bbox,
            ocr_confidence=round(avg_conf, 1),
        ))
        line_counter += 1

    _t.ROW_MERGE_PX = orig
    return sigs


# ── Processor ─────────────────────────────────────────────────────────────────

class VisionProcessor(BasePDFProcessor):
    """Google Cloud Vision OCR backend — handles handwritten petition forms."""

    def extract(self, pdf_path: Path) -> list[ExtractedSignature]:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)

        images   = _load_images_pil(pdf_path)
        all_sigs: list[ExtractedSignature] = []
        line_counter = 1

        for page_num, image in enumerate(images, start=1):
            words = _vision_words(image)

            if _is_vision_block_format(words):
                page_sigs = _extract_vision_block(
                    words, image, page_num, line_counter
                )
            else:
                page_sigs = _extract_vision_columns(
                    words, image, page_num, line_counter
                )

            line_counter += len(page_sigs)
            all_sigs.extend(page_sigs)

        return all_sigs
