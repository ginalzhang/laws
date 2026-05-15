# CLAUDE.md — Petition Verifier

## What this app is
Petition signature verification (OCR → fuzzy match to voter roll → fraud flags → staff
review UI) **plus** field-worker management (shifts, payroll, schedule, leaderboard).
Two product areas in one codebase — keep an eye on the seams.

## Run, test, deploy
```bash
pip install -e ".[dev]"          # install with dev tools
pvfy serve                        # FastAPI on :8000 (entry: petition_verifier.api:app)
pvfy process FILE.pdf --save-db --summary
pytest tests/ -v                  # only tests/test_matching.py has real coverage today
ruff check src tests              # lint (configured below)
ruff format src tests             # format
```
Deploy is Render via `render.yaml` + `Procfile`. Python pinned to 3.11.

## Architecture map (so you don't search)
- `src/petition_verifier/api.py` — FastAPI app, wires routers, big legacy file.
- `src/petition_verifier/routes/` — domain routers: `auth`, `worker`, `shift`, `schedule`, `payroll`, `leaderboard`, `payment`.
- `src/petition_verifier/storage/database.py` — **single 961-line file** holding every SQLAlchemy model + access helper. Treat it as a known smell, not a pattern to repeat.
- `src/petition_verifier/ingestion/` — OCR backends: `tesseract.py`, `vision.py` (Google), `reducto.py`. Selected via `OCR_BACKEND` env var.
- `src/petition_verifier/matching/` — `voter_matcher.py`, `address_normalizer.py`, `duplicate_detector.py`, `fraud_detector.py`. **This is the only well-tested area.**
- `ui/` — static HTML/JS (no framework). `dashboard.html` is 103KB — be selective with reads.

## Non-obvious gotchas (read before editing)

1. **`postgres://` URLs break SQLAlchemy 2.x.** Render gives you `postgres://...`; the app
   must rewrite to `postgresql://`. Don't undo that translation if you see it.
2. **Database schema is Alembic-only.** Do not reintroduce `Base.metadata.create_all()`
   in app startup. New DBs use `pvfy db upgrade`; existing production DBs with the
   baseline schema are stamped once with `pvfy db stamp head` before automatic
   deploy-time upgrades are enabled.
3. **Login users are database-backed.** Create admins with `pvfy admin create-user`
   after migrations. For first deploy bootstrap only, set
   `PVFY_BOOTSTRAP_ADMIN_EMAIL` + `PVFY_BOOTSTRAP_ADMIN_PASSWORD`; remove the
   password env var after the account exists. For the private owner account,
   set `PVFY_OWNER_EMAIL` + `PVFY_OWNER_PASSWORD` to recreate it as `boss`
   after a DB wipe. Browser auth uses httpOnly `access_token`/`refresh_token`
   cookies; do not reintroduce localStorage bearer-token auth, source-code login
   names, or source-code passwords.
4. **`tempfile.NamedTemporaryFile(delete=False)` is used in upload paths.** Always wrap
   in try/finally and `os.unlink` — leaked temp files have already been an issue.
5. **N+1 query history.** Recent commits fixed several. When touching `routes/payroll_routes.py`,
   `routes/shift_routes.py`, or `storage/database.py`, use SQLAlchemy `selectinload`/`joinedload`
   for collections rather than lazy access in a loop.
6. **Tab/data caching.** `Cache tab data so switching tabs is instant` introduced a client-side
   cache. If you fix a bug and it still appears, hard-reload (cmd+shift+R) before assuming you're wrong.
7. **`.env` is gitignored.** Don't commit it, don't print its values into logs, and don't add
   new "fix-*" unauthenticated admin endpoints — there used to be `/fix-activate-users` and
   `/fix-reset-boss`; both were removed for being open password-reset holes. Don't reintroduce.
8. **OCR backends have different output shapes.** Reducto returns structured fields; Tesseract +
   Vision return raw text the pipeline parses. Mock at the `ExtractedSignature` boundary, not at the OCR call.
9. **The matching layer uses dataclasses, the rest of the app uses Pydantic.** This is intentional
   (matching is pure logic, Pydantic is for API edges). Don't homogenize without a reason.

## Test discipline
- Coverage is ~3% of LOC. Any non-trivial change to `routes/*` or `api.py` should add at least
  a smoke test with `httpx.AsyncClient` against the app — there are zero such tests today,
  so you are setting the precedent.
- The matching layer has real unit tests; follow their style (`tests/test_matching.py`).
- Don't mock the database; use SQLite in-memory or a tempfile DB. We have a history of
  mocked tests passing while real queries failed.

## Style
- Ruff handles formatting and lint — don't hand-format. Config is in `pyproject.toml`.
- Type hints required on new/changed public functions; existing untyped code is OK as-is.
- Match the existing import order in each file rather than reordering on edit.

## Sub-agents available
Located in `.claude/agents/`. Use these instead of generic instructions:
- `security-reviewer` — read-only audit of changes for auth/secret/SQL-injection issues.
- `python-test-writer` — writes pytest + httpx tests for FastAPI routes.
- `route-extractor` — splits oversized routers; knows the project's router pattern.
- `code-simplifier` — Boris's "simplify" pass: dead code, redundant checks, over-abstraction.

## What NOT to do
- Don't rename the `bangui` branch; it's the working branch.
- Don't reintroduce open-admin endpoints (`/fix-*`, debug routes without auth).
- Don't refactor `database.py` or `api.py` in a single PR — too large, too entangled.
  Extract a slice (one domain at a time) and ship incrementally.
- Don't add `print()` debugging that ships — use `logging` (already imported in most modules).
- Don't add a frontend framework (React/Vue). The UI is intentionally vanilla.
- Don't bypass `ruff` with `# noqa` blanket suppressions; fix the rule or narrow the ignore.
