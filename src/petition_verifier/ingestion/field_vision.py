"""
Field-level Google Cloud Vision OCR for petition signature sheets.

Architecture per page
──────────────────────
1. OpenCV preprocessing
   - deskew via minAreaRect
   - perspective / border crop
   - shadow removal (divide-by-blur)
   - CLAHE contrast normalisation
   - grayscale normalisation

2. Header isolation
   - detect signature-table bounding box using horizontal lines
   - default fallback: ignore top 35 % of image
   - NEVER OCR the preamble / legal description

3. Row detection
   - morphological horizontal-line detection → row y-bands
   - fallback: divide grid evenly

4. Field-level OCR (per row)
   - crop: name / street_address / city / zip / date
   - send each crop SEPARATELY to Vision document_text_detection
   - reject text matching petition-header noise patterns
   - reject excessively long strings (> 60 chars)
   - normalise: UPPERCASE names, standardise address abbreviations,
     validate ZIP, title-case city

5. Signature detection (ink density — no Vision call)
   - grayscale → adaptive threshold → remove table lines
   - dark-pixel ratio in the signature column crop

6. Page fingerprinting  (dHash — no external library)
   - 64-bit perceptual hash of the cleaned/cropped grid area

7. Row fingerprinting
   - composite: normalised_name + normalised_address + dHash(row_crop)

8. Page versioning & duplicate detection
   - compare extracted rows against stored PageUploadLineRow records
   - classify: blank / new_signature / already_counted / changed_needs_review

9. Structured JSON output  (matches spec)
"""
from __future__ import annotations

import io
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from ..models import BoundingBox, ExtractedSignature
from .pdf_processor import BasePDFProcessor


# ── Header noise rejection ────────────────────────────────────────────────────

_HEADER_NOISE_RE = re.compile(
    r"\b(petition|initiative|county|election|circulator|registered|voter|"
    r"secretary|measure|ordinance|statute|section|paragraph|fiscal|impact|"
    r"sponsor|assembly|senate|district|government|pursuant|hereby|whereas|"
    r"certify|circulated|declaration|proponent|signatures|qualified)\b",
    re.I,
)
_MAX_FIELD_CHARS = 60   # any OCR result longer than this is almost certainly noise


def _is_header_noise(text: str) -> bool:
    if not text:
        return False
    if len(text) > _MAX_FIELD_CHARS:
        return True
    return bool(_HEADER_NOISE_RE.search(text))


# ── dHash perceptual fingerprint ─────────────────────────────────────────────

def _dhash(img: Image.Image, size: int = 8) -> str:
    """64-bit difference hash — no external library required."""
    gray = img.convert("L").resize((size + 1, size), Image.LANCZOS)
    px = list(gray.getdata())
    bits = [
        "1" if px[r * (size + 1) + c] > px[r * (size + 1) + c + 1] else "0"
        for r in range(size)
        for c in range(size)
    ]
    return format(int("".join(bits), 2), "016x")


def _hamming(a: str, b: str) -> int:
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


def page_fingerprint(img: Image.Image) -> str:
    return _dhash(img, size=8)


def row_fingerprint(row_img: Image.Image, norm_name: str, norm_addr: str) -> str:
    img_hash = _dhash(row_img, size=6)
    text = f"{norm_name}|{norm_addr}".upper()
    return f"{text}#{img_hash}"


def pages_likely_same(fp_a: str, fp_b: str, threshold: int = 10) -> bool:
    """True when Hamming distance ≤ threshold (out of 64 bits)."""
    return _hamming(fp_a, fp_b) <= threshold


def rows_likely_same(fp_a: str, fp_b: str) -> float:
    """Return similarity 0-1. ≥ 0.75 → same person."""
    try:
        text_a, hash_a = fp_a.split("#", 1)
        text_b, hash_b = fp_b.split("#", 1)
    except ValueError:
        return 0.0

    from rapidfuzz import fuzz as _fuzz
    text_score = _fuzz.token_sort_ratio(text_a, text_b) / 100.0
    hash_score = 1.0 - _hamming(hash_a, hash_b) / 36.0   # 6×6 = 36 bits
    return text_score * 0.70 + hash_score * 0.30


# ── Normalisation ─────────────────────────────────────────────────────────────

_ADDR_ABBR: dict[str, str] = {
    r"\bSTREET\b": "ST",   r"\bAVENUE\b": "AVE",  r"\bBOULEVARD\b": "BLVD",
    r"\bDRIVE\b":  "DR",   r"\bROAD\b":   "RD",   r"\bLANE\b":  "LN",
    r"\bCOURT\b":  "CT",   r"\bPLACE\b":  "PL",   r"\bCIRCLE\b": "CIR",
    r"\bHIGHWAY\b":"HWY",  r"\bAPARTMENT\b":"APT",r"\bSUITE\b": "STE",
    r"\bNORTH\b":  "N",    r"\bSOUTH\b":  "S",    r"\bEAST\b":  "E",
    r"\bWEST\b":   "W",
}
_ABBR_KEEP = {"ST","AVE","BLVD","DR","RD","LN","CT","PL","CIR","HWY","APT","STE",
              "N","S","E","W","NE","NW","SE","SW"}
ZIP_RE = re.compile(r"^\d{5}(-\d{4})?$")


def _clean(s: str) -> str:
    s = "".join(c for c in s if unicodedata.category(c)[0] != "C")
    return re.sub(r"\s+", " ", s).strip()


def normalize_name(raw: str) -> str:
    s = _clean(raw).upper()
    s = re.sub(r"[^A-Z\s'\-\.]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_address(raw: str) -> str:
    s = _clean(raw).upper()
    for pat, rep in _ADDR_ABBR.items():
        s = re.sub(pat, rep, s)
    words = [w if w in _ABBR_KEEP else w.title() for w in s.split()]
    return " ".join(words)


def normalize_city(raw: str) -> str:
    s = _clean(raw)
    s = re.sub(r"[^A-Za-z\s\-]", "", s)
    return s.strip().title()


def normalize_zip(raw: str) -> tuple[str, bool]:
    digits = re.sub(r"[^\d\-]", "", raw.strip())
    if ZIP_RE.match(digits):
        return digits, True
    found = re.findall(r"\d{5}", raw)
    if found:
        return found[0], True
    return raw.strip(), False


# ── OpenCV preprocessing ──────────────────────────────────────────────────────

def _pil_to_cv(img: Image.Image) -> np.ndarray:
    return np.array(img.convert("RGB"))[:, :, ::-1]


def _cv_to_pil(arr: np.ndarray) -> Image.Image:
    if arr.ndim == 2:
        return Image.fromarray(arr, mode="L").convert("RGB")
    return Image.fromarray(arr[:, :, ::-1])


def preprocess_image(pil_img: Image.Image) -> Image.Image:
    """Full OpenCV preprocessing. Falls back to Pillow if OpenCV unavailable."""
    try:
        import cv2
        arr = _pil_to_cv(pil_img)
        result = _cv_pipeline(arr)
        return _cv_to_pil(result)
    except ImportError:
        return _pil_fallback(pil_img)


def _pil_fallback(img: Image.Image) -> Image.Image:
    from PIL import ImageEnhance, ImageFilter
    img = img.convert("L")
    s = min(img.width, img.height)
    if s < 2000:
        scale = 2000 / s
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    img = ImageEnhance.Contrast(img).enhance(1.8)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    img = img.filter(Image.ImageFilter.UnsharpMask(radius=1, percent=150, threshold=3))
    return img.convert("RGB")


def _cv_pipeline(arr: np.ndarray) -> np.ndarray:
    import cv2

    # Upscale to at least 2000px on the short side
    h, w = arr.shape[:2]
    if min(h, w) < 2000:
        sc = 2000 / min(h, w)
        arr = cv2.resize(arr, (int(w * sc), int(h * sc)), interpolation=cv2.INTER_LANCZOS4)
        h, w = arr.shape[:2]

    gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)

    # Shadow removal: divide by blurred background estimate
    bg = cv2.GaussianBlur(gray, (51, 51), 0)
    norm = cv2.divide(gray, bg, scale=255)

    # CLAHE
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(norm)

    # Deskew
    deskewed = _deskew_cv(enhanced)

    # Crop to content (removes dark camera borders)
    cropped = _crop_content_cv(deskewed)

    return cv2.cvtColor(cropped, cv2.COLOR_GRAY2BGR)


def _deskew_cv(gray: np.ndarray) -> np.ndarray:
    import cv2
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    coords = np.column_stack(np.where(thresh > 0))
    if len(coords) < 50:
        return gray
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.3:
        return gray
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(gray, M, (w, h),
                          flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _crop_content_cv(gray: np.ndarray) -> np.ndarray:
    import cv2
    _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inv = cv2.bitwise_not(thresh)
    pts = cv2.findNonZero(inv)
    if pts is None:
        return gray
    x, y, w, h = cv2.boundingRect(pts)
    m = 20
    return gray[max(0, y - m):y + h + m, max(0, x - m):x + w + m]


# ── Grid / table detection ────────────────────────────────────────────────────

def _cluster_ints(vals: list[int], gap: int = 8) -> list[int]:
    if not vals:
        return []
    clusters: list[list[int]] = [[vals[0]]]
    for v in vals[1:]:
        if v - clusters[-1][-1] <= gap:
            clusters[-1].append(v)
        else:
            clusters.append([v])
    return [int(np.mean(c)) for c in clusters]


def detect_table_bbox(pil_img: Image.Image) -> tuple[int, int, int, int]:
    """
    Return (x, y, w, h) of the signature table.
    Ignores the top petition-header section.

    Primary: OpenCV horizontal-line detection to find the first table line.
    Fallback: treat top 35% as header, remaining 58% as table.
    """
    try:
        import cv2
        gray = np.array(pil_img.convert("L"))
        h, w = gray.shape

        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        # Look for horizontal lines spanning ≥ 30% width
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 5, 80), 1))
        h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
        proj = np.sum(h_lines, axis=1).astype(int)
        threshold = int(w * 255 * 0.25)
        raw_ys = [y for y, v in enumerate(proj) if v > threshold]
        ys = _cluster_ints(raw_ys, gap=8)

        # CA initiative petitions have a large header (top funders, notice to public)
        # plus an "All signers..." sub-header row at the top of the table.
        # Signer rows reliably start in the bottom 42% of the page.
        grid_ys = [y for y in ys if y > h * 0.55]
        if len(grid_ys) >= 3:
            table_top    = grid_ys[0]
            # Stop before Declaration of Circulator (bottom ~10%)
            table_bottom = min(grid_ys[-1], int(h * 0.88))
            return 0, table_top, w, table_bottom - table_top

    except ImportError:
        pass

    # Fallback: signer rows occupy roughly 57-88% of page height
    h, w = pil_img.height, pil_img.width
    table_top = int(h * 0.57)
    return 0, table_top, w, int(h * 0.31)


def detect_rows(pil_img: Image.Image, table_bbox: tuple[int, int, int, int]) -> list[tuple[int, int]]:
    """
    Detect signer row y-bands within the table area.
    Returns list of (y_top, y_bottom) pairs, up to 8 rows.
    """
    tx, ty, tw, th = table_bbox
    img_w, img_h = pil_img.width, pil_img.height
    y_end = min(ty + th, img_h)

    try:
        import cv2
        region = np.array(pil_img.convert("L"))[ty:y_end, tx:tx + tw]
        rh, rw = region.shape

        _, binary = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(rw // 5, 50), 1))
        h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)
        proj = np.sum(h_lines, axis=1).astype(int)
        threshold = int(rw * 255 * 0.20)
        raw_ys = [y for y, v in enumerate(proj) if v > threshold]
        rel_ys = _cluster_ints(raw_ys, gap=6)
        abs_ys = [ty + y for y in rel_ys]

        if len(abs_ys) >= 4:
            min_h = max(8, (abs_ys[-1] - abs_ys[0]) // 12)
            max_h = (abs_ys[-1] - abs_ys[0]) // 2
            rows = [
                (abs_ys[i], abs_ys[i + 1])
                for i in range(len(abs_ys) - 1)
                if min_h <= abs_ys[i + 1] - abs_ys[i] <= max_h
            ]
            return rows[:8]

    except ImportError:
        pass

    # Fallback: divide grid evenly into 8 rows
    row_h = th // 8
    return [(ty + i * row_h, ty + (i + 1) * row_h) for i in range(8)]


def detect_columns(pil_img: Image.Image, table_bbox: tuple[int, int, int, int]) -> dict[str, tuple[int, int]]:
    """
    Detect column x-boundaries from vertical printed lines.
    Returns dict: {field_name: (x_lo, x_hi)}
    Falls back to proportional bands.
    """
    tx, ty, tw, th = table_bbox
    w = pil_img.width
    field_names = ["sig", "name", "street", "city", "zip", "date"]

    try:
        import cv2
        region = np.array(pil_img.convert("L"))[ty:ty + th, tx:tx + tw]
        rh, rw = region.shape

        _, binary = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        v_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(rh // 7, 15)))
        v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_kernel)
        proj = np.sum(v_lines, axis=0).astype(int)
        threshold = int(rh * 255 * 0.10)
        raw_xs = [x for x, v in enumerate(proj) if v > threshold]
        rel_xs = _cluster_ints(raw_xs, gap=10)
        abs_xs = [tx + x for x in rel_xs]

        if len(abs_xs) >= 4:
            n = min(len(abs_xs) - 1, len(field_names))
            cols = {field_names[i]: (abs_xs[i], abs_xs[i + 1]) for i in range(n)}
            # Fill missing columns with proportional fallback
            defaults = _proportional_cols(w)
            for name in field_names:
                if name not in cols:
                    cols[name] = defaults[name]
            return cols

    except ImportError:
        pass

    return _proportional_cols(w)


def _proportional_cols(width: int) -> dict[str, tuple[int, int]]:
    # CA initiative petition format:
    #   [row#] | [Print Name / Signature] | [Residence Address] | [City] | [Zip]
    # No date column. Proportions measured from standard CA petition scans.
    bands = {
        "sig":    (0.07, 0.43),   # left section: signature (bottom half of row block)
        "name":   (0.07, 0.43),   # left section: print name (top half of row block)
        "street": (0.43, 0.69),   # middle: residence address
        "city":   (0.69, 0.86),   # right-middle: city
        "zip":    (0.86, 0.97),   # far right: zip
        "date":   (0.94, 0.97),   # no date column → tiny edge area returns empty
    }
    return {name: (int(lo * width), int(hi * width)) for name, (lo, hi) in bands.items()}


# ── Signature detection (ink density) ────────────────────────────────────────

_SIG_INK_THRESHOLD = 0.04   # 4% dark pixels
_SIG_DARK_CUTOFF   = 80     # pixel value ≤ 80/255 → "dark"


def _remove_table_lines(gray_arr: np.ndarray) -> np.ndarray:
    """Subtract detected horizontal/vertical lines so only ink remains."""
    try:
        import cv2
        h, w = gray_arr.shape
        _, binary = cv2.threshold(gray_arr, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        h_k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(w // 4, 30), 1))
        v_k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(h // 4, 10)))
        h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_k)
        v_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, v_k)
        lines = cv2.add(h_lines, v_lines)
        cleaned = cv2.subtract(binary, lines)
        # Convert back: ink = dark on white
        return cv2.bitwise_not(cleaned)
    except ImportError:
        return gray_arr


def detect_signature(crop: Image.Image) -> bool:
    """
    True if the signature column crop contains handwritten ink.
    Uses ink-density analysis — no OCR.
    """
    gray = np.array(crop.convert("L"))
    no_lines = _remove_table_lines(gray)
    dark = np.sum(no_lines < _SIG_DARK_CUTOFF)
    ratio = float(dark) / max(no_lines.size, 1)
    return ratio >= _SIG_INK_THRESHOLD


def ink_density(crop: Image.Image) -> float:
    """Return fraction of pixels darker than cutoff (0–1)."""
    gray = np.array(crop.convert("L"))
    return float(np.sum(gray < _SIG_DARK_CUTOFF)) / max(gray.size, 1)


# ── Vision OCR ────────────────────────────────────────────────────────────────

def _make_client():
    from .vision import _make_vision_client
    return _make_vision_client()


def _pil_to_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _vision_ocr_field(crop: Image.Image, client) -> tuple[str, str]:
    """
    Run Vision document_text_detection on a single field crop.
    Returns (raw_text, confidence_level) where level ∈ {high, medium, low, none}.
    Rejects header noise and excessively long strings.
    """
    from google.cloud import vision as gv

    response = client.document_text_detection(
        image=gv.Image(content=_pil_to_bytes(crop)),
    )
    if response.error.message:
        return "", "none"

    text = (response.full_text_annotation.text or "").strip()
    text = " ".join(text.split())   # collapse whitespace / newlines

    if _is_header_noise(text):
        return "", "none"

    # Confidence
    confs = [
        word.confidence
        for page in response.full_text_annotation.pages
        for block in page.blocks
        for para in block.paragraphs
        for word in para.words
    ]
    if not confs:
        return text, "none"
    avg = sum(confs) / len(confs)
    level = "high" if avg >= 0.80 else "medium" if avg >= 0.50 else "low"
    return text, level


# ── Cell crop ─────────────────────────────────────────────────────────────────

def _crop_cell(img: Image.Image, y_top: int, y_bot: int,
               x_lo: int, x_hi: int, pad: int = 4) -> Image.Image:
    W, H = img.width, img.height
    return img.crop((
        max(0, x_lo - pad),
        max(0, y_top - pad),
        min(W, x_hi + pad),
        min(H, y_bot + pad),
    ))


# ── Row extraction ────────────────────────────────────────────────────────────

def _flags(row: dict) -> list[str]:
    f = []
    if not row["name"]["raw"]:              f.append("missing_name")
    if not row["street_address"]["raw"]:    f.append("missing_street")
    if not row["city"]["raw"]:              f.append("missing_city")
    if not row["zip"]["raw"]:               f.append("missing_zip")
    if not row["date"]["raw"]:              f.append("missing_date")
    if not row["signature_present"]:        f.append("no_signature")
    if not row["zip"]["valid_format"] and row["zip"]["raw"]:
        f.append("invalid_zip_format")
    if row["name"]["ocr_confidence"] == "low":     f.append("low_confidence_name")
    if row["street_address"]["ocr_confidence"] == "low": f.append("low_confidence_address")
    return f


def _extract_row(
    preprocessed: Image.Image,
    y_top: int,
    y_bot: int,
    col_xs: dict[str, tuple[int, int]],
    row_number: int,
    client,
) -> tuple[dict, Image.Image]:
    """
    Extract one signer row.
    Returns (row_dict, row_crop_image) for fingerprinting.
    """
    W = preprocessed.width
    def _band(name: str) -> tuple[int, int]:
        return col_xs.get(name, (int(0.03 * W), int(0.97 * W)))

    fields = {
        "name":   _band("name"),
        "street": _band("street"),
        "city":   _band("city"),
        "zip":    _band("zip"),
        "date":   _band("date"),
    }

    crops: dict[str, Image.Image] = {
        f: _crop_cell(preprocessed, y_top, y_bot, xlo, xhi)
        for f, (xlo, xhi) in fields.items()
    }

    # Parallel Vision calls for text fields
    results: dict[str, tuple[str, str]] = {}
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_vision_ocr_field, crops[f], client): f for f in fields}
        for fut in as_completed(futures):
            fd = futures[fut]
            try:
                results[fd] = fut.result()
            except Exception:
                results[fd] = ("", "none")

    # Signature detection (ink density)
    sig_xlo, sig_xhi = _band("sig")
    sig_crop = _crop_cell(preprocessed, y_top, y_bot, sig_xlo, sig_xhi)
    sig_present = detect_signature(sig_crop)

    name_raw,   name_conf   = results.get("name",   ("", "none"))
    street_raw, street_conf = results.get("street", ("", "none"))
    city_raw,   city_conf   = results.get("city",   ("", "none"))
    zip_raw,    zip_conf    = results.get("zip",    ("", "none"))
    date_raw,   date_conf   = results.get("date",   ("", "none"))
    zip_norm, zip_valid = normalize_zip(zip_raw)

    row_crop = _crop_cell(preprocessed, y_top, y_bot, 0, W)
    row = {
        "row_number": row_number,
        "name": {
            "raw":            name_raw,
            "normalized":     normalize_name(name_raw),
            "ocr_confidence": name_conf,
        },
        "street_address": {
            "raw":            street_raw,
            "normalized":     normalize_address(street_raw),
            "ocr_confidence": street_conf,
        },
        "city": {
            "raw":            city_raw,
            "normalized":     normalize_city(city_raw),
            "ocr_confidence": city_conf,
        },
        "zip": {
            "raw":          zip_raw,
            "normalized":   zip_norm,
            "valid_format": zip_valid,
        },
        "date": {
            "raw":            date_raw,
            "normalized":     _clean(date_raw),
            "ocr_confidence": date_conf,
        },
        "signature_present": sig_present,
        "flags": [],
        # Will be filled in during versioning step
        "status": "blank",
        "row_fingerprint": "",
    }
    row["flags"] = _flags(row)
    return row, row_crop


def _row_is_occupied(row: dict) -> bool:
    """True if the row appears to have been filled in."""
    return bool(row["name"]["raw"] or row["street_address"]["raw"] or row["signature_present"])


# ── Page extraction ───────────────────────────────────────────────────────────

def extract_page(
    pil_img: Image.Image,
    page_id: str,
    page_num: int,
    client,
    prev_rows: Optional[list[dict]] = None,
) -> dict:
    """
    Full pipeline for one petition page.

    prev_rows: list of row dicts from previous uploads of the same page.
               If provided, versioning/duplicate detection is applied.

    Returns a page_result dict matching the spec JSON schema.
    """
    # 1. Preprocess
    preprocessed = preprocess_image(pil_img)

    # 2. Compute page fingerprint
    fp = page_fingerprint(preprocessed)

    # 3. Detect table bbox (ignores preamble/header)
    bbox = detect_table_bbox(preprocessed)

    # 4. Detect rows and columns within the table
    row_ys  = detect_rows(preprocessed, bbox)
    col_xs  = detect_columns(preprocessed, bbox)

    # 5. Extract each row
    rows: list[dict] = []
    for i, (y_top, y_bot) in enumerate(row_ys, start=1):
        row, row_crop = _extract_row(preprocessed, y_top, y_bot, col_xs, i, client)
        # Compute row fingerprint
        norm_name = row["name"]["normalized"]
        norm_addr = row["street_address"]["normalized"]
        row["row_fingerprint"] = row_fingerprint(row_crop, norm_name, norm_addr)

        if not _row_is_occupied(row):
            row["status"] = "blank"
        else:
            row["status"] = "new_signature"   # default; versioning step may override

        rows.append(row)

    # 6. Versioning / duplicate detection
    if prev_rows:
        _apply_versioning(rows, prev_rows)

    # 7. Summary
    new_sigs       = sum(1 for r in rows if r["status"] == "new_signature")
    already_counted= sum(1 for r in rows if r["status"] == "already_counted")
    needs_review   = sum(1 for r in rows if r["status"] == "changed_needs_review")
    blank          = sum(1 for r in rows if r["status"] == "blank")

    return {
        "page_id":          page_id,
        "page_fingerprint": fp,
        "summary": {
            "total_rows_detected":  len(rows),
            "new_signatures":       new_sigs,
            "previously_counted":   already_counted,
            "needs_review":         needs_review,
            "blank":                blank,
        },
        "rows": rows,
    }


# ── Versioning ────────────────────────────────────────────────────────────────

_SAME_ROW_THRESHOLD = 0.75   # similarity ≥ 0.75 → same person
_CHANGED_THRESHOLD  = 0.45   # 0.45–0.75 → might be same, needs review


def _apply_versioning(new_rows: list[dict], prev_rows: list[dict]) -> None:
    """
    Mutate new_rows[].status based on comparison with prev_rows.

    already_counted      — same fingerprint as a counted previous row
    changed_needs_review — partially matches a previous row (possible edit or re-scan)
    new_signature        — not seen before and has content
    blank                — no content
    """
    prev_fps = [r.get("row_fingerprint", "") for r in prev_rows
                if r.get("status") in ("new_signature", "already_counted")]

    for row in new_rows:
        if row["status"] == "blank":
            continue
        fp = row["row_fingerprint"]
        best_score = 0.0
        for prev_fp in prev_fps:
            score = rows_likely_same(fp, prev_fp)
            if score > best_score:
                best_score = score

        if best_score >= _SAME_ROW_THRESHOLD:
            row["status"] = "already_counted"
        elif best_score >= _CHANGED_THRESHOLD:
            row["status"] = "changed_needs_review"
        # else: remains "new_signature"


# ── Claude disambiguation (ambiguous rows) ───────────────────────────────────

def claude_resolve_ambiguous(
    rows: list[dict],
    prev_rows: list[dict],
) -> list[dict]:
    """
    Send rows marked 'changed_needs_review' to Claude for disambiguation.
    Returns the same rows with updated status where Claude is confident.

    Claude is used ONLY here — not as memory, not to count signatures.
    The DB remains the source of truth.
    """
    import os, json
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return rows

    ambiguous = [r for r in rows if r["status"] == "changed_needs_review"]
    if not ambiguous:
        return rows

    import httpx as _httpx
    _hdrs = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }

    prompt_rows = []
    for r in ambiguous:
        prev_matches = []
        fp = r.get("row_fingerprint", "")
        for pr in prev_rows:
            score = rows_likely_same(fp, pr.get("row_fingerprint", ""))
            if score >= _CHANGED_THRESHOLD:
                prev_matches.append({
                    "name":    pr.get("name", {}).get("normalized", ""),
                    "address": pr.get("street_address", {}).get("normalized", ""),
                    "score":   round(score, 2),
                })
        prompt_rows.append({
            "row_number":       r["row_number"],
            "new_name":         r["name"]["normalized"],
            "new_address":      r["street_address"]["normalized"],
            "has_signature":    r["signature_present"],
            "previous_matches": prev_matches,
        })

    prompt = (
        "You are reviewing petition signature rows. For each row, determine if it is "
        "the SAME person as a previous match (should be 'already_counted'), a DIFFERENT "
        "new signer ('new_signature'), or truly ambiguous ('changed_needs_review').\n\n"
        "Rows:\n" + json.dumps(prompt_rows, indent=2) + "\n\n"
        "Return ONLY a JSON array: [{\"row_number\": N, \"verdict\": \"already_counted|new_signature|changed_needs_review\"}]"
    )

    try:
        with _httpx.Client(http2=False, timeout=30) as _http:
            _r = _http.post(
                "https://api.anthropic.com/v1/messages",
                headers=_hdrs,
                json={"model": "claude-haiku-4-5-20251001", "max_tokens": 512,
                      "messages": [{"role": "user", "content": prompt}]},
            )
        _r.raise_for_status()
        text = _r.json()["content"][0]["text"].strip()
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        verdicts = {v["row_number"]: v["verdict"] for v in json.loads(text)}
        for row in rows:
            if row["row_number"] in verdicts:
                row["status"] = verdicts[row["row_number"]]
    except Exception:
        pass   # keep existing status if Claude fails

    return rows


# ── ExtractedSignature conversion ─────────────────────────────────────────────

def _conf_to_float(level: str) -> float:
    return {"high": 90.0, "medium": 65.0, "low": 35.0, "none": 0.0}.get(level, 0.0)


def page_result_to_signatures(page_result: dict, page_num: int, line_start: int = 1) -> list[ExtractedSignature]:
    """Convert a page_result dict to a list of ExtractedSignature objects."""
    sigs: list[ExtractedSignature] = []
    line_no = line_start
    for row in page_result.get("rows", []):
        if row.get("status") not in ("new_signature", "changed_needs_review"):
            continue
        n = row["name"]
        sa = row["street_address"]
        city = row["city"]
        z = row["zip"]
        addr_parts = [sa["normalized"] or sa["raw"],
                      city["normalized"] or city["raw"],
                      z["normalized"] or z["raw"]]
        raw_addr = ", ".join(p for p in addr_parts if p)
        conf = (_conf_to_float(n["ocr_confidence"]) + _conf_to_float(sa["ocr_confidence"])) / 2
        sigs.append(ExtractedSignature(
            line_number=line_no,
            page=page_num,
            raw_name=n["raw"],
            raw_address=raw_addr,
            raw_date=row["date"]["raw"],
            signature_present=row["signature_present"],
            ocr_confidence=round(conf, 1),
        ))
        line_no += 1
    return sigs


# ── FieldVisionProcessor ──────────────────────────────────────────────────────

class FieldVisionProcessor(BasePDFProcessor):
    """
    Field-level Google Cloud Vision OCR backend.

    Processes one field cell at a time instead of the whole page,
    dramatically reducing cross-field contamination and header noise.

    Set OCR_BACKEND=vision_field in .env to use this processor.
    """

    def extract(self, pdf_path: Path) -> list[ExtractedSignature]:
        pages = self.extract_rich(pdf_path)
        sigs: list[ExtractedSignature] = []
        line = 1
        for i, page in enumerate(pages, start=1):
            page_sigs = page_result_to_signatures(page, i, line)
            sigs.extend(page_sigs)
            line += len(page_sigs)
        return sigs

    def extract_rich(
        self,
        pdf_path: Path,
        prev_rows_by_page: Optional[dict[int, list[dict]]] = None,
    ) -> list[dict]:
        """
        Returns one page_result dict per page.
        prev_rows_by_page: {page_num: [row_dicts_from_previous_uploads]}
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(pdf_path)

        from .vision import _load_images_pil
        images = _load_images_pil(pdf_path)
        client = _make_client()
        pages: list[dict] = []

        for page_num, pil_img in enumerate(images, start=1):
            page_id   = f"{pdf_path.stem}_p{page_num}"
            prev_rows = (prev_rows_by_page or {}).get(page_num)
            result    = extract_page(pil_img, page_id, page_num, client, prev_rows)
            pages.append(result)

        return pages
