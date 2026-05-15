"""
Tesseract OCR backend.

Supports two petition form layouts, auto-detected per page:

  COLUMN FORMAT  — signer data is in table columns (NAME | ADDRESS | DATE | SIG)
                   Common for county-printed multi-column sheets.

  BLOCK FORMAT   — each signer has labeled fields stacked vertically:
                     Print Name: ___________
                     Residence Address (Only): ___________
                     City: _______ State: __ Zip: _______
                     Date: _______  Signature: ___
                   Standard California initiative petition layout.

Auto-detection: if we find 3+ "Print Name:" labels on a page → block format.
Otherwise → column format.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pytesseract
from pdf2image import convert_from_path
from PIL import Image

from ..models import BoundingBox, ExtractedSignature
from .pdf_processor import BasePDFProcessor

# ── tunables ─────────────────────────────────────────────────────────────────
DPI            = 400    # higher DPI = better handwriting recognition
ROW_MERGE_PX   = 20
MIN_CONF       = 15     # lower threshold to catch faint handwriting
MIN_WORDS_PER_ROW = 1   # block format can have single-word fields

# Block-format: how far below a "Print Name:" label to search for the value (px)
LABEL_VALUE_SCAN_PX = 80   # generous — handwriting can sit further from the label
LABEL_GROUP_PX      = 220  # full signer block height (sig + name + addr + city + date)

# OCR configs
_PSM6  = "--psm 6"   # uniform block — good for detecting printed labels
_PSM11 = "--psm 11"  # sparse text — finds any text including handwriting
_PSM7  = "--psm 7"   # single text line — for reading one field at a time

# Column-format bands (fallback)
DEFAULT_BANDS = {
    "name":    (0.00, 0.38),
    "address": (0.38, 0.74),
    "date":    (0.74, 0.88),
    "sig":     (0.88, 1.00),
}

HEADER_ALIASES = {
    "name":    {"name", "full name", "print name", "signer"},
    "address": {"address", "residence address", "addr", "street"},
    "date":    {"date", "signed", "date signed"},
    "sig":     {"signature", "sig", "sign here"},
}

# Block-format label patterns (case-insensitive)
_LABEL_NAME    = re.compile(r"print\s*name", re.I)
_LABEL_ADDR    = re.compile(r"residence\s*address|res\.?\s*address", re.I)
_LABEL_CITY    = re.compile(r"\bcity\b", re.I)
_LABEL_ZIP     = re.compile(r"\bzip\b", re.I)
_LABEL_DATE    = re.compile(r"\bdate\b", re.I)
_LABEL_SIG     = re.compile(r"\bsignature\b", re.I)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _Word:
    text: str
    conf: float
    left: int
    top: int
    width: int
    height: int

    @property
    def cx(self) -> int:
        return self.left + self.width // 2

    @property
    def cy(self) -> int:
        return self.top + self.height // 2

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height


@dataclass
class _Row:
    words: list[_Word] = field(default_factory=list)

    @property
    def top(self) -> int:
        return min(w.top for w in self.words)

    @property
    def bottom(self) -> int:
        return max(w.top + w.height for w in self.words)

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def text_in_band(self, band: tuple[float, float], page_width: int) -> str:
        lo = int(band[0] * page_width)
        hi = int(band[1] * page_width)
        words = sorted(
            [w for w in self.words if lo <= w.cx < hi],
            key=lambda w: w.left,
        )
        return " ".join(w.text for w in words).strip()


def _parse_words(image: Image.Image, config: str = _PSM6) -> list[_Word]:
    data = pytesseract.image_to_data(
        image, output_type=pytesseract.Output.DICT, config=config
    )
    words = []
    for i, text in enumerate(data["text"]):
        text = text.strip()
        conf = float(data["conf"][i])
        if not text or conf < MIN_CONF:
            continue
        words.append(_Word(
            text=text, conf=conf,
            left=data["left"][i], top=data["top"][i],
            width=data["width"][i], height=data["height"][i],
        ))
    return words


def _ocr_crop(image: Image.Image, top: int, bottom: int, padding: int = 10) -> list[_Word]:
    """
    Crop a horizontal band of the image and run sparse OCR on it.
    Returns words with y-coordinates relative to the original image.
    """
    h = image.height
    y1 = max(0, top - padding)
    y2 = min(h, bottom + padding)
    crop = image.crop((0, y1, image.width, y2))
    words = _parse_words(crop, config=_PSM11)
    # Translate y back to original image coordinates
    for w in words:
        w.top += y1
    return words


def _cluster_rows(words: list[_Word]) -> list[_Row]:
    if not words:
        return []
    sorted_words = sorted(words, key=lambda w: w.top)
    rows: list[_Row] = [_Row([sorted_words[0]])]
    for word in sorted_words[1:]:
        if word.top - rows[-1].top <= ROW_MERGE_PX:
            rows[-1].words.append(word)
        else:
            rows.append(_Row([word]))
    return [r for r in rows if len(r.words) >= MIN_WORDS_PER_ROW]


# ── Column-format helpers ─────────────────────────────────────────────────────

def _detect_column_bands(rows: list[_Row], page_width: int) -> dict[str, tuple[float, float]]:
    for row in rows[:5]:
        matched: dict[str, int] = {}
        for col, aliases in HEADER_ALIASES.items():
            for word in row.words:
                if word.text.lower() in aliases:
                    matched[col] = word.cx
                    break
        if len(matched) >= 3:
            sorted_cols = sorted(matched.items(), key=lambda x: x[1])
            bands: dict[str, tuple[float, float]] = {}
            for idx, (col, cx) in enumerate(sorted_cols):
                if idx == 0:
                    lo, hi = 0.0, (cx + sorted_cols[1][1]) / 2 / page_width
                elif idx == len(sorted_cols) - 1:
                    lo, hi = (sorted_cols[idx-1][1] + cx) / 2 / page_width, 1.0
                else:
                    lo = (sorted_cols[idx-1][1] + cx) / 2 / page_width
                    hi = (cx + sorted_cols[idx+1][1]) / 2 / page_width
                bands[col] = (round(lo, 3), round(hi, 3))
            return bands
    return DEFAULT_BANDS


def _sig_present(row: _Row, band: tuple[float, float], page_width: int) -> tuple[bool, BoundingBox | None]:
    lo = int(band[0] * page_width)
    hi = int(band[1] * page_width)
    words_in_sig = [w for w in row.words if lo <= w.cx < hi]
    if not words_in_sig:
        return False, None
    bbox = BoundingBox(
        x=min(w.left for w in words_in_sig),
        y=row.top,
        width=hi - lo,
        height=row.height,
    )
    return True, bbox


def _is_header_row(row: _Row) -> bool:
    text = " ".join(w.text.lower() for w in row.words)
    hits = sum(1 for hw in {"name","address","date","signature","city","zip","state"} if hw in text)
    return hits >= 3


def _first_header_bottom(rows: list[_Row]) -> int | None:
    for row in rows[:8]:
        if _is_header_row(row):
            return row.bottom
    return None


def _strip_leading_line_number(text: str) -> str:
    return re.sub(r"^\s*\d+\s+", "", text).strip()


# ── Block-format helpers ──────────────────────────────────────────────────────

def _is_block_format(words: list[_Word]) -> bool:
    """Return True if this page uses labeled signer blocks (CA initiative format)."""
    full_text = " ".join(w.text for w in words)
    name_hits = len(_LABEL_NAME.findall(full_text))
    return name_hits >= 2


def _words_after_label(label_word: _Word, all_words: list[_Word], page_width: int) -> str:
    """
    Get text to the RIGHT of a label word on the same row,
    OR text on the immediately following row within LABEL_VALUE_SCAN_PX.
    Stops at the next label-like word.
    """
    # Same-row words to the right of the label
    same_row = [
        w for w in all_words
        if abs(w.cy - label_word.cy) <= ROW_MERGE_PX
        and w.left > label_word.right + 5
        and not _is_label_word(w.text)
    ]
    if same_row:
        return " ".join(w.text for w in sorted(same_row, key=lambda w: w.left))

    # Next row(s) within scan distance
    below = [
        w for w in all_words
        if label_word.bottom < w.top <= label_word.bottom + LABEL_VALUE_SCAN_PX
        and not _is_label_word(w.text)
    ]
    if below:
        # Cluster into first row only
        first_y = min(w.top for w in below)
        first_row = [w for w in below if w.top - first_y <= ROW_MERGE_PX]
        return " ".join(w.text for w in sorted(first_row, key=lambda w: w.left))

    return ""


_LABEL_WORDS = re.compile(
    r"^(print|name|residence|address|city|state|zip|date|signature|only|res\.?)$",
    re.I
)

def _is_label_word(text: str) -> bool:
    return bool(_LABEL_WORDS.match(text.strip(":")))


def _extract_block_format(
    words: list[_Word], image: Image.Image, page_num: int, line_start: int
) -> list[ExtractedSignature]:
    """
    Parse a CA-style block petition page.

    Two-pass strategy:
      Pass 1 (PSM 6, already done): find "Print Name:" label positions.
      Pass 2 (PSM 11, crop): re-OCR just each signer block to pick up handwriting.
    """
    page_width = image.width

    # Find all "Print Name" label occurrences (from the printed-text pass)
    name_labels: list[_Word] = []
    for i, w in enumerate(words):
        if _LABEL_NAME.search(w.text):
            name_labels.append(w)
        elif w.text.lower() == "print" and i + 1 < len(words):
            nxt = words[i + 1]
            if nxt.text.lower() in ("name", "name:") and abs(nxt.cy - w.cy) <= ROW_MERGE_PX:
                name_labels.append(w)

    sigs: list[ExtractedSignature] = []

    for idx, label in enumerate(name_labels):
        block_top    = label.top - 15
        block_bottom = label.top + LABEL_GROUP_PX

        # Pass 2: re-OCR the signer block with sparse text mode
        block_words = _ocr_crop(image, block_top, block_bottom)

        # Also include pass-1 words for label detection fallback
        p1_block = [w for w in words if block_top <= w.top <= block_bottom]
        all_block = {id(w): w for w in block_words + p1_block}
        bw = list(all_block.values())

        # ── Name ──────────────────────────────────────────────────────────
        name_label_in_block = next(
            (w for w in bw if _LABEL_NAME.search(w.text)), label
        )
        raw_name = _words_after_label(name_label_in_block, bw, page_width)

        # ── Address ────────────────────────────────────────────────────────
        addr_label = next((w for w in bw if _LABEL_ADDR.search(w.text)), None)
        raw_address = _words_after_label(addr_label, bw, page_width) if addr_label else ""

        city_label = next(
            (w for w in bw
             if _LABEL_CITY.search(w.text) and not _LABEL_ADDR.search(w.text)),
            None
        )
        city_text = _words_after_label(city_label, bw, page_width) if city_label else ""
        if city_text:
            raw_address = f"{raw_address}, {city_text}".strip(", ")

        # ── Date ───────────────────────────────────────────────────────────
        date_label = next((w for w in bw if _LABEL_DATE.search(w.text)), None)
        raw_date = _words_after_label(date_label, bw, page_width) if date_label else ""

        # ── Signature presence ─────────────────────────────────────────────
        sig_label = next((w for w in bw if _LABEL_SIG.search(w.text)), None)
        sig_text  = _words_after_label(sig_label, bw, page_width) if sig_label else ""
        sig_present = bool(sig_text.strip())

        sig_bbox = None
        if sig_label:
            sig_bbox = BoundingBox(
                x=sig_label.left,
                y=sig_label.top,
                width=page_width - sig_label.left,
                height=LABEL_GROUP_PX // 3,
                page=page_num,
            )

        avg_conf = sum(w.conf for w in bw) / max(len(bw), 1)

        sigs.append(ExtractedSignature(
            line_number=line_start + idx,
            page=page_num,
            raw_name=raw_name.strip(),
            raw_address=raw_address.strip(),
            raw_date=raw_date.strip(),
            signature_present=sig_present,
            signature_bbox=sig_bbox,
            ocr_confidence=round(avg_conf, 1),
        ))

    return sigs


# ── Main processor ────────────────────────────────────────────────────────────

IMAGE_SUFFIXES = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp"}


def _load_images(path: Path) -> list[Image.Image]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return convert_from_path(str(path), dpi=DPI)
    if suffix in IMAGE_SUFFIXES:
        import pillow_heif
        pillow_heif.register_heif_opener()
        img = Image.open(path).convert("RGB")
        return [img]
    raise ValueError(
        f"Unsupported file type: {suffix}. "
        f"Supported: .pdf, .heic, .heif, .jpg, .png, .tiff"
    )


class TesseractProcessor(BasePDFProcessor):
    """Extract petition signatures using local Tesseract."""

    def extract(self, pdf_path: Path) -> list[ExtractedSignature]:
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)

        images = _load_images(pdf_path)
        all_sigs: list[ExtractedSignature] = []
        line_counter = 1

        for page_num, image in enumerate(images, start=1):
            page_width = image.width
            words = _parse_words(image)

            if _is_block_format(words):
                page_sigs = _extract_block_format(
                    words, image, page_num, line_counter
                )
            else:
                rows  = _cluster_rows(words)
                bands = _detect_column_bands(rows, page_width)
                header_bottom = _first_header_bottom(rows)
                page_sigs = []

                for row in rows:
                    if _is_header_row(row):
                        continue
                    if header_bottom is not None and row.top <= header_bottom:
                        continue
                    name_text    = _strip_leading_line_number(
                        row.text_in_band(bands["name"], page_width)
                    )
                    address_text = row.text_in_band(bands["address"], page_width)
                    date_text    = row.text_in_band(bands["date"],    page_width)
                    sig_present, sig_bbox = _sig_present(row, bands["sig"], page_width)
                    if sig_bbox:
                        sig_bbox.page = page_num
                    if not name_text and not address_text:
                        continue
                    avg_conf = sum(w.conf for w in row.words) / len(row.words)
                    page_sigs.append(ExtractedSignature(
                        line_number=line_counter + len(page_sigs),
                        page=page_num,
                        raw_name=name_text,
                        raw_address=address_text,
                        raw_date=date_text,
                        signature_present=sig_present,
                        signature_bbox=sig_bbox,
                        ocr_confidence=round(avg_conf, 1),
                    ))

            line_counter += len(page_sigs)
            all_sigs.extend(page_sigs)

        return all_sigs
