# Agent Operating Guide

## Purpose
This repo is a FastAPI + static HTML app for petition signature verification, review packets, workforce management, payroll, and field dashboards.

## Fast Start
```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
python -m pytest tests/test_matching.py -v
PYTHONPATH=src python -m uvicorn petition_verifier.api:app --host 0.0.0.0 --port 8000
```

System packages are required for OCR/PDF work:
```bash
brew install tesseract poppler
```

## Common Commands
```bash
make setup       # install local dev package
make compile     # syntax/import-bytecode check for src and tests
make test-fast   # matching + extraction ensemble tests
make check-system-deps # confirm Poppler/Tesseract are installed
make test        # run full pytest suite against committed fixtures
make fixtures    # intentionally regenerate committed test fixtures
make run         # start FastAPI on localhost:8000
make smoke-local # curl a running local server like CI does
```

Use Python 3.11. The repo has `.python-version` set to 3.11.9, CI uses 3.11, and the app is not expected to import under macOS system Python 3.9.
Make targets use `.venv/bin/python` automatically when the venv exists.

## Where Things Live
- `src/petition_verifier/api.py`: FastAPI app setup, static UI serving, router registration, legacy petition/project endpoints, seed/fix endpoints.
- `src/petition_verifier/routes/`: modular FastAPI routers for auth, review packets, workers, shifts, payroll, schedule, stats, teams, locations, and reflections.
- `src/petition_verifier/storage/database.py`: SQLAlchemy models and the main `Database` service; Alembic owns schema mutation.
- `src/petition_verifier/ingestion/`: OCR/PDF/image backends for Tesseract, Google Vision-style extraction, Claude, Reducto, and field-specific parsing.
- `src/petition_verifier/matching/`: address normalization, voter matching, duplicate detection, and fraud heuristics.
- `src/petition_verifier/extraction/`: ensemble extraction/reconciliation logic.
- `src/petition_verifier/payroll/`: payroll calculations.
- `ui/`: standalone HTML/CSS/JS pages. There is no frontend build system.
- `tests/`: focused tests for matching, pipeline, and extraction ensemble behavior.

## Before Editing
- Check both `api.py` and `routes/` before adding or changing an endpoint.
- Check `ui/*.html` fetch calls before changing route paths or response shapes.
- Avoid broad refactors of large files unless the task explicitly asks for them.
- Keep generated/runtime state out of commits: `.env`, `*.db`, `.venv/`, `packet_uploads/`, caches, and local OCR outputs.
- Prefer small docs/tooling changes over product behavior changes when improving agent experience.

## Verification
```bash
python -m compileall -q src tests
python -m pytest tests/test_matching.py tests/test_extraction_ensemble.py tests/test_app_smoke.py -v
python -m pytest tests/ -v
```

For local API smoke checks:
```bash
make run
# in another shell
make smoke-local
```

## Runtime Gotchas
- Local DB defaults to `sqlite:///./petition_verifier.db`.
- Review packet uploads use `packet_uploads/` relative to the process working directory.
- OCR behavior changes with `OCR_BACKEND`, `ANTHROPIC_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, and `REDUCTO_API_KEY`.
- Startup creates/updates permanent demo accounts in `api.py`.
- Some auth/dev shortcuts and maintenance endpoints are intentionally present today; document behavior before changing it.
- `pyproject.toml` is the local source of truth. Use `pip install -e '.[dev]'` for normal work and add `.[vision]` when changing Google Vision or field-vision OCR paths.
- `requirements.txt` is a legacy pinned snapshot; `requirements-deploy.txt` is for Render.

## Large/Risky Files
- `ui/dashboard.html`: large management UI with embedded CSS/JS and direct API calls.
- `src/petition_verifier/storage/database.py`: ORM schema and persistence methods in one file; schema changes must use Alembic.
- `src/petition_verifier/api.py`: app composition plus legacy endpoints and startup side effects.
- `src/petition_verifier/routes/review_routes.py`: packet upload/review, OCR fallbacks, voter matching, fraud analysis, and export.
- `src/petition_verifier/ingestion/vision.py` and `field_vision.py`: OCR heuristics that are sensitive to image layout and external credentials.

See `docs/ARCHITECTURE.md`, `docs/ROUTES.md`, `docs/FRONTEND.md`, `docs/DATABASE.md`, `docs/TESTING.md`, and `docs/RISKS.md` before making cross-cutting changes.

## Progress Log
After each agent-experience cycle, update `.context/agent-codebase-grade.md` with:
- previous grade and new grade
- highest-impact improvement made
- verification run
- next bottleneck
