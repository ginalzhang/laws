# Petition Verifier — Setup & Usage

## System dependencies (one-time)

```bash
# macOS
brew install tesseract poppler

# Ubuntu/Debian
apt-get install tesseract-ocr poppler-utils
```

## Python install

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
```

## Configure

```bash
cp .env.example .env
# Edit .env:
#   VOTER_ROLL_CSV=path/to/your/voter_roll.csv
#   DATABASE_URL=sqlite:///./petition_verifier.db  (default)
```

## Voter roll CSV format

Required columns (case-insensitive):

| Column | Example |
|--------|---------|
| voter_id | CA-00123456 |
| first_name | Jane |
| last_name | Smith |
| street_address | 123 Main St |
| city | Springfield |
| state | CA |
| zip_code | 90210 |

Validate your CSV before running:
```bash
pvfy import-voters path/to/voter_roll.csv
```

## Process a petition PDF

```bash
# Quick summary
pvfy process petition.pdf --summary

# Full JSON output
pvfy process petition.pdf --output results.json

# Save to database and print summary
pvfy process petition.pdf --save-db --summary

# Override voter roll for this run
pvfy process petition.pdf --voter-roll /other/voter_roll.csv --summary
```

## Process a whole folder

```bash
pvfy batch ./petitions/ --output-dir ./results/
```

## Review UI

```bash
# Process some PDFs first (saves to DB automatically with --save-db)
pvfy process petition.pdf --save-db --summary

# Start the server
pvfy serve

# Open in browser
open http://localhost:8000
```

The UI shows each signature as green (approved) / yellow (needs review) / red (rejected).
Staff only needs to review the yellow rows — typically 20-30% of submissions.

## Swap to Reducto OCR (when ready)

1. Add your API key to `.env`:
   ```
   OCR_BACKEND=reducto
   REDUCTO_API_KEY=your_key_here
   ```
2. No code changes needed — the pipeline picks up the env var automatically.

## Tuning match thresholds

Default: auto-approve ≥85, review 70-84, reject <70.

After running against your first real project, compare results to your manual review.
If too many valid sigs are being rejected, lower `THRESHOLD_APPROVE` to 80.

```bash
# In .env:
THRESHOLD_APPROVE=80
THRESHOLD_REVIEW=65
```

## Run tests

```bash
# Unit tests (no PDF required)
python -m pytest tests/test_matching.py -v

# Integration test (uses committed fixtures)
python -m pytest tests/ -v
```

Regenerate fixtures only when intentionally updating `tests/fixtures/`:
```bash
make fixtures
```

For agent-oriented architecture, route, and testing notes, see `AGENTS.md` and `docs/`.
