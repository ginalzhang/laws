---
name: petition-verifier-gotchas
description: Load when editing FastAPI routes, SQLAlchemy models, OCR pipeline, or auth code in the petition-verifier repo. Lists the project-specific failure modes you must avoid.
---

# Petition-Verifier Gotchas (accumulated mistakes)

Add to this file whenever you (or a future agent) make a mistake the codebase
"should have warned about." Treat as a living lessons-learned log.

## Database

- **Render passes `postgres://` URLs.** SQLAlchemy 2.x rejects them — rewrite to
  `postgresql://` on load. The translation already exists; don't undo it.
- **One shared DB connection pool.** A recent commit introduced this for perf. If
  you see `create_engine` in a new place, you're doing it wrong — use the existing
  session factory from `storage/`.
- **No real migrations workflow yet.** `alembic` is in deps but unused. If you change
  a model, document the manual migration step in the PR.

## Routes / FastAPI

- Every state-mutating endpoint needs `Depends(get_current_user)`. Past `/fix-*`
  endpoints were unauthenticated password-reset holes — removed; do not reintroduce.
- N+1 query patterns historically hit `payroll_routes.py` and `shift_routes.py`.
  Prefer `selectinload` over lazy-load-in-a-loop.
- `tempfile.NamedTemporaryFile(delete=False)` in upload paths must be paired with
  `os.unlink` in a `finally:` block, or files leak into prod disk.

## OCR pipeline

- Three OCR backends with different output shapes (Tesseract, Google Vision, Reducto).
  Selected by `OCR_BACKEND` env. Mock at the `ExtractedSignature` boundary, never at
  the OCR call site.
- Vision API requires `GOOGLE_APPLICATION_CREDENTIALS` set; don't import the google
  client at module top level — import inside the function so missing creds don't
  break unrelated startup paths.

## Auth quirks

- Login is username-only. `_PERMANENT_USERS` list in `api.py` is recreated on every
  startup. Don't "fix" it without coordination — these are the only working real-user
  accounts.
- Workers log in by single name with `password123` (yes, really — known short-term tradeoff).

## Frontend

- `ui/dashboard.html` is 103KB of vanilla JS. Don't read it whole; grep for the
  function you need.
- Tab/data is client-cached. After fixing a server bug, hard-reload (cmd+shift+R)
  before assuming the fix didn't work.

## When you add to this file

Format: `- **<one-line headline>.** <one-line explanation>. <one-line fix or
direction>.` Keep entries short. Group by section. Delete obsolete entries.
