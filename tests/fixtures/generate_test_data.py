"""
Generate a synthetic voter roll CSV and a matching petition PDF for testing.

    python tests/fixtures/generate_test_data.py

Outputs:
  tests/fixtures/voter_roll.csv
  tests/fixtures/sample_petition.pdf
"""
from __future__ import annotations

import csv
import random
import sys
from pathlib import Path

# Requires reportlab and faker — install with:
#   pip install reportlab faker
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas
    from faker import Faker
except ImportError:
    print("Install dev dependencies: pip install 'petition-verifier[dev]'")
    sys.exit(1)

OUT_DIR = Path(__file__).parent
fake    = Faker("en_US")
random.seed(42)

STATES = ["CA", "AZ", "CO", "NV", "OR"]

# ── Voter roll ────────────────────────────────────────────────────────────────

def generate_voter_roll(n: int = 500) -> list[dict]:
    voters = []
    for i in range(1, n + 1):
        voters.append({
            "voter_id":       f"VR-{i:06d}",
            "first_name":     fake.first_name(),
            "last_name":      fake.last_name(),
            "street_address": fake.street_address(),
            "city":           fake.city(),
            "state":          random.choice(STATES),
            "zip_code":       fake.zipcode(),
        })
    return voters


def save_voter_roll(voters: list[dict], path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(voters[0].keys()))
        w.writeheader()
        w.writerows(voters)
    print(f"Voter roll written: {path} ({len(voters)} records)")


# ── Petition PDF ──────────────────────────────────────────────────────────────

def generate_petition_pdf(
    voters: list[dict],
    path: Path,
    n_sigs: int = 20,
    error_rate: float = 0.15,    # fraction of rows with OCR-like noise
    dupe_rate: float = 0.05,     # fraction of duplicate rows
) -> None:
    """
    Creates a realistic-looking petition sheet PDF with:
      - Column headers: NAME | ADDRESS | DATE | SIGNATURE
      - n_sigs rows of signer data
      - error_rate rows have minor OCR-style misspellings
      - dupe_rate rows duplicate an earlier signer
    """
    c   = canvas.Canvas(str(path), pagesize=letter)
    W, H = letter

    MARGIN = 0.6 * inch
    COL_X  = {
        "line":  MARGIN,
        "name":  MARGIN + 0.4 * inch,
        "addr":  MARGIN + 2.8 * inch,
        "date":  MARGIN + 5.8 * inch,
        "sig":   MARGIN + 7.0 * inch,
    }
    ROW_H  = 0.38 * inch
    HEADER_Y = H - MARGIN - 0.6 * inch
    FIRST_ROW_Y = HEADER_Y - ROW_H * 1.2

    # Title
    c.setFont("Helvetica-Bold", 13)
    c.drawString(MARGIN, H - MARGIN - 0.25 * inch, "INITIATIVE PETITION — SIGNATURE SHEET")

    # Column headers
    c.setFont("Helvetica-Bold", 8)
    c.drawString(COL_X["name"], HEADER_Y, "PRINT NAME")
    c.drawString(COL_X["addr"], HEADER_Y, "RESIDENCE ADDRESS")
    c.drawString(COL_X["date"], HEADER_Y, "DATE")
    c.drawString(COL_X["sig"],  HEADER_Y, "SIGNATURE")

    # Header underline
    c.setLineWidth(0.5)
    c.line(MARGIN, HEADER_Y - 4, W - MARGIN, HEADER_Y - 4)

    chosen  = random.sample(voters, min(n_sigs, len(voters)))
    seen    = []
    c.setFont("Helvetica", 8)

    def _mangle(s: str) -> str:
        """Simulate OCR noise."""
        replacements = [("a", "@"), ("o", "0"), ("l", "1"), ("i", "!"), ("e", "3")]
        for a, b in random.sample(replacements, k=1):
            s = s.replace(a, b, 1)
        return s

    for idx, voter in enumerate(chosen):
        y = FIRST_ROW_Y - idx * ROW_H
        if y < MARGIN:
            c.showPage()
            y = H - MARGIN - ROW_H

        is_error = random.random() < error_rate
        is_dupe  = random.random() < dupe_rate and seen

        if is_dupe:
            original = random.choice(seen)
            name    = original["name"]
            address = original["address"]
        else:
            name    = f"{voter['first_name']} {voter['last_name']}"
            address = f"{voter['street_address']}, {voter['city']}, {voter['state']}"
            seen.append({"name": name, "address": address})

        if is_error:
            name    = _mangle(name)
            address = _mangle(address)

        date_str = fake.date_between(start_date="-90d", end_date="today").strftime("%m/%d/%Y")

        c.setFont("Helvetica", 8)
        c.drawString(COL_X["line"], y, str(idx + 1))
        c.drawString(COL_X["name"], y, name[:38])
        c.drawString(COL_X["addr"], y, address[:48])
        c.drawString(COL_X["date"], y, date_str)
        # Simulate a signature mark
        c.setFont("Helvetica-Oblique", 9)
        c.drawString(COL_X["sig"], y, f"/{voter['last_name'].lower()}/")
        c.setFont("Helvetica", 8)

        # Row divider
        c.setLineWidth(0.2)
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.line(MARGIN, y - 6, W - MARGIN, y - 6)
        c.setStrokeColorRGB(0, 0, 0)

    c.save()
    print(f"Sample petition PDF written: {path} ({n_sigs} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    voters = generate_voter_roll(500)
    save_voter_roll(voters, OUT_DIR / "voter_roll.csv")
    generate_petition_pdf(voters, OUT_DIR / "sample_petition.pdf", n_sigs=30)
    print("\nDone. Run the pipeline:")
    print("  VOTER_ROLL_CSV=tests/fixtures/voter_roll.csv pvfy process tests/fixtures/sample_petition.pdf --summary")
