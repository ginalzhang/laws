from __future__ import annotations

from PIL import Image, ImageDraw

from petition_verifier.matching.handwriting import (
    find_similar_handwriting,
    handwriting_hash,
)


def _row(path, strokes: list[tuple[int, int, int, int]]) -> None:
    img = Image.new("RGB", (320, 56), "white")
    draw = ImageDraw.Draw(img)
    # Printed table lines should not dominate the hash.
    draw.line((0, 0, 319, 0), fill="black", width=1)
    draw.line((0, 55, 319, 55), fill="black", width=1)
    draw.line((22, 0, 22, 55), fill="black", width=1)
    draw.line((138, 0, 138, 55), fill="black", width=1)
    for stroke in strokes:
        draw.line(stroke, fill="black", width=3)
    img.save(path)


def test_handwriting_hash_ignores_blank_row(tmp_path):
    blank = tmp_path / "blank.jpg"
    _row(blank, [])

    assert handwriting_hash(blank) is None


def test_find_similar_handwriting_pairs_same_strokes(tmp_path):
    row1 = tmp_path / "row1.jpg"
    row2 = tmp_path / "row2.jpg"
    row3 = tmp_path / "row3.jpg"
    same = [(35, 16, 78, 36), (78, 36, 112, 15), (42, 42, 120, 42)]
    different = [(40, 44, 110, 12), (45, 14, 92, 14)]
    _row(row1, same)
    _row(row2, same)
    _row(row3, different)

    pairs = find_similar_handwriting({
        1: str(row1),
        2: str(row2),
        3: str(row3),
    })

    assert any(left == 1 and right == 2 for left, right, _distance in pairs)
