# Architecture Map

## Runtime Shape
```text
browser UI in ui/*.html
        |
        v
FastAPI app: src/petition_verifier/api.py
        |
        +-- modular routers in src/petition_verifier/routes/
        +-- pipeline: PDF/image -> OCR -> normalize -> match -> detect dupes
        +-- storage: SQLAlchemy models and Database service
```

The frontend is static HTML/CSS/JS served by FastAPI. There is no Node build, shared API client, or bundler.

## Backend Subsystems
- `api.py`: creates the FastAPI app, mounts `/static`, registers routers, serves login/canvasser/field-manager/evann pages, owns legacy project processing endpoints, and runs startup side effects.
- `routes/auth_routes.py`: login flows, dev token, active users, field-manager password/user helpers, scan login, logout, current user.
- `routes/review_routes.py`: review packet uploads, packet lines, image serving, county/voter-roll matching, fraud analysis, decisions, and export.
- `routes/worker_routes.py`, `shift_routes.py`, `schedule_routes.py`, `payroll_routes.py`, `payment_routes.py`, `stats_routes.py`, `team_routes.py`, `location_routes.py`, `reflection_routes.py`: workforce and field operations APIs.
- `storage/database.py`: ORM table definitions, startup `create_all` plus best-effort `ALTER TABLE`, and persistence methods.
- `pipeline.py`: classic petition verification orchestration.
- `ingestion/`: OCR and document extraction backends.
- `matching/`: normalization, voter roll matching, duplicate detection, and fraud detection.
- `models.py`: shared Pydantic models for extraction, matching, verification, and project summaries.

## Main Data Flows
- Classic CLI/API verification: `pvfy process` or `/process` -> `Pipeline` -> `ingestion.get_processor()` -> `matching` -> `ProjectResult` -> optional `Database.save_project`.
- Review center upload: browser `/review/upload` -> `routes/review_routes.py` -> cleaned/uploaded file paths -> OCR/extraction -> `review_packets` and `review_packet_lines`.
- Workforce/payroll: UI routes call worker/shift/stats/payroll routers -> `Database` methods -> SQLAlchemy rows.
- Static UI: `/`, `/canvasser`, `/field-manager`, `/evann`, and `/static/*` are served from `ui/`.

## Large File Map
| File | Why risky | Editing guidance |
| --- | --- | --- |
| `ui/dashboard.html` | Largest file; embedded CSS/JS; direct fetch calls to auth, projects, review, and fraud endpoints. | Search for the endpoint and nearby state/render functions before editing. Keep changes local. |
| `src/petition_verifier/storage/database.py` | ORM schema, startup schema mutation, and all persistence methods are combined. | Avoid schema changes unless the DB migration behavior is explicitly addressed. |
| `src/petition_verifier/api.py` | App composition, startup users, legacy endpoints, and maintenance endpoints are mixed. | Check router modules before adding new routes. Be careful with auth expectations. |
| `src/petition_verifier/routes/review_routes.py` | Upload processing, OCR fallbacks, matching, fraud analysis, and export live together. | Preserve existing fallback order unless the task is about OCR behavior. |
| `src/petition_verifier/ingestion/vision.py` | Google Vision-style OCR heuristics and layout parsing. | Test with fixture generation and avoid broad heuristic rewrites. |
| `src/petition_verifier/ingestion/field_vision.py` | Field-specific extraction and handwriting/row heuristics. | Treat thresholds and model calls as behavior changes. |

## Dependency Sources
- Local agent default: `pip install -e '.[dev]'`.
- Vision/OCR-agent work: `pip install -e '.[dev,vision]'`.
- CI: installs `pip install -e '.[dev]'`.
- Render: installs `requirements-deploy.txt`.

`pyproject.toml` is the local and CI package source of truth. `requirements.txt` is a legacy pinned snapshot; `requirements-deploy.txt` remains for Render deploy compatibility.
