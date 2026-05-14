# Risks And Gotchas

This file documents current behavior for future agents. Do not treat these notes as approval to expand risky patterns.

## Security/Auth
- `api.py` recreates permanent startup accounts and resets their passwords on every startup.
- `auth_routes.py` includes passwordless name login, a dev token path gated by `DEV_AUTO_LOGIN=true`, field-manager password helpers, and scan login.
- Several app-level endpoints in `api.py` are public or have unclear role checks, including project browsing/processing, fraud scan, seed/demo, and fix endpoints.
- CI currently logs in with the hardcoded startup boss account.

Changing any of this is a product/security decision. If a task touches auth, document the intended access model before editing.

## Database And Runtime State
- Default DB is `sqlite:///./petition_verifier.db`.
- PostgreSQL URLs starting with `postgres://` are rewritten to `postgresql://`.
- Tables are created with `Base.metadata.create_all`.
- Some schema changes are applied by best-effort `ALTER TABLE` statements at startup and errors are swallowed.
- Review packet files are stored in `packet_uploads/` relative to the process working directory.
- Render deploys may have ephemeral local filesystem behavior, so DB rows can outlive uploaded files unless persistent storage is configured.

## OCR And External Services
- `OCR_BACKEND=tesseract` is the default classic pipeline backend.
- `OCR_BACKEND=vision` needs `GOOGLE_APPLICATION_CREDENTIALS`.
- `OCR_BACKEND=vision_field` needs `GOOGLE_APPLICATION_CREDENTIALS`.
- `OCR_BACKEND=claude` needs `ANTHROPIC_API_KEY`.
- `OCR_BACKEND=reducto` needs `REDUCTO_API_KEY`.
- Review packet processing has additional Claude/Google Vision fallback behavior controlled by available keys.
- Startup performs an Anthropic connectivity check if `ANTHROPIC_API_KEY` is set.

## Dependency Gotchas
- `pyproject.toml` is the local package source of truth.
- Use `pip install -e '.[dev,vision]'` when changing Google Vision or field-vision OCR paths.
- `requirements.txt` is a legacy pinned snapshot and should not be used for normal agent setup.
- `requirements-deploy.txt` is what Render installs.
- `.python-version` is `3.11.9`; `pyproject.toml` requires Python `>=3.11`; CI uses Python 3.11.
- Fresh macOS venvs may start with old pip versions that cannot install this `pyproject.toml` package in editable mode; run `python -m pip install --upgrade pip` inside the venv first.

## Large File Map
| File | Risk |
| --- | --- |
| `ui/dashboard.html` | Large embedded frontend; endpoint changes can silently break UI flows. |
| `src/petition_verifier/storage/database.py` | Schema, migrations-by-startup, and data access are combined. |
| `src/petition_verifier/api.py` | Startup side effects and public/legacy endpoints are mixed with app setup. |
| `src/petition_verifier/routes/review_routes.py` | Review packet flow combines upload, OCR, voter matching, fraud analysis, and export. |
| `src/petition_verifier/ingestion/vision.py` | OCR/layout heuristics are fragile and credential-dependent. |
| `src/petition_verifier/ingestion/field_vision.py` | Field-specific extraction and handwriting heuristics are threshold-sensitive. |
