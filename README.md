# Petition Verifier

FastAPI backend and static HTML UI for petition signature verification, review packets, canvasser/field-manager workflows, workforce management, and payroll.

## Quick Start
```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
cp .env.example .env
python -m pytest tests/test_matching.py -v
make run
```

Open `http://localhost:8000`.

Install OCR/PDF system packages before running OCR flows:
```bash
brew install tesseract poppler
```

## Common Commands
```bash
make setup       # pip install -e '.[dev]'
make compile     # compile src and tests
make test-fast   # fast, no-server test subset
make check-system-deps # confirm Poppler/Tesseract are installed
make test        # full pytest against committed fixtures
make fixtures    # intentionally regenerate committed test fixtures
make run         # local FastAPI server
make smoke-local # smoke a running local server
```

Make targets use `.venv/bin/python` automatically when the venv exists.

## Where To Change Things
| Area | Start here |
| --- | --- |
| App startup, static UI serving, legacy project endpoints | `src/petition_verifier/api.py` |
| Auth, review packets, workers, shifts, payroll, stats, teams | `src/petition_verifier/routes/` |
| ORM tables and persistence methods | `src/petition_verifier/storage/database.py` |
| OCR/PDF/image extraction | `src/petition_verifier/ingestion/` |
| Voter matching, normalization, duplicates, fraud heuristics | `src/petition_verifier/matching/` |
| Shared result models | `src/petition_verifier/models.py` |
| Static browser UI | `ui/*.html`, `ui/sw.js` |
| CLI | `src/petition_verifier/cli/main.py` |

Check both `api.py` and `routes/` before adding endpoints. Check `ui/*.html` before changing API paths or response shapes.

## Docs For Agents
- `AGENTS.md`: concise repo operating guide.
- `docs/ARCHITECTURE.md`: subsystem map and data flow.
- `docs/ROUTES.md`: route ownership and auth notes.
- `docs/FRONTEND.md`: static UI entry pages, state keys, and endpoint coupling.
- `docs/DATABASE.md`: table groups, runtime DB setup, and schema-change rules.
- `docs/TESTING.md`: setup, test, smoke, and CI-equivalent commands.
- `docs/RISKS.md`: security/runtime gotchas and large-file map.
- `SETUP.md`: original user-facing setup and CLI guide.

## Dependency Policy
For local development and coding agents, prefer:
```bash
pip install -e '.[dev]'
```

Current caveats:
- Upgrade pip first in a fresh macOS venv; old pip versions cannot install this `pyproject.toml` package in editable mode.
- Use Python 3.11. `.python-version` is 3.11.9 and CI uses 3.11.
- Install `.[vision]` when working on Google Vision or field-vision OCR paths.
- `requirements.txt` is a legacy pinned snapshot; Render installs `requirements-deploy.txt`.
