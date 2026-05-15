"""Image-level handwriting similarity helpers for review row crops."""
from __future__ import annotations

from pathlib import Path

from PIL import Image


def _flat_pixels(image: Image.Image) -> list[int]:
    getter = getattr(image, "get_flattened_data", None)
    if getter:
        return list(getter())
    return list(image.getdata())


def _ink_mask(image: Image.Image) -> list[list[bool]]:
    gray = image.convert("L")
    width, height = gray.size
    pixels = _flat_pixels(gray)
    mask = [
        [pixels[y * width + x] < 170 for x in range(width)]
        for y in range(height)
    ]

    # Remove long printed table rules before hashing handwriting.
    for y, row in enumerate(mask):
        if sum(row) / max(1, width) > 0.45:
            mask[y] = [False] * width
    for x in range(width):
        if sum(mask[y][x] for y in range(height)) / max(1, height) > 0.55:
            for y in range(height):
                mask[y][x] = False
    return mask


def handwriting_hash(path: str | Path) -> int | None:
    """Return a compact perceptual hash for handwriting ink in a row crop."""
    with Image.open(path) as image:
        image = image.convert("RGB")
        width, height = image.size
        # Petition handwriting is concentrated in the print/signature column.
        region = image.crop((
            int(width * 0.07),
            0,
            int(width * 0.43),
            height,
        ))
        mask = _ink_mask(region)
        ink = sum(1 for row in mask for value in row if value)
        if ink < 18:
            return None

        cleaned = Image.new("L", region.size, 255)
        px = cleaned.load()
        for y, row in enumerate(mask):
            for x, value in enumerate(row):
                if value:
                    px[x, y] = 0
        small = cleaned.resize((32, 16), Image.Resampling.LANCZOS)
        values = _flat_pixels(small)
        mean = sum(values) / len(values)
        bits = 0
        for value in values:
            bits = (bits << 1) | int(value < mean)
        return bits


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def find_similar_handwriting(
    crop_paths: dict[int, str],
    *,
    max_distance: int = 18,
) -> list[tuple[int, int, int]]:
    """Return pairs of line numbers whose row-crop handwriting hashes match."""
    hashes: dict[int, int] = {}
    for line_no, path in crop_paths.items():
        try:
            value = handwriting_hash(path)
        except Exception:
            value = None
        if value is not None:
            hashes[line_no] = value

    pairs: list[tuple[int, int, int]] = []
    line_numbers = sorted(hashes)
    for i, left in enumerate(line_numbers):
        for right in line_numbers[i + 1:]:
            distance = hamming_distance(hashes[left], hashes[right])
            if distance <= max_distance:
                pairs.append((left, right, distance))
    return pairs
