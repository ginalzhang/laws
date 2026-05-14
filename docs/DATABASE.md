# Database Map

Persistence is centralized in `src/petition_verifier/storage/database.py`. It contains SQLAlchemy table classes, startup schema creation/patching, and the `Database` service methods used by routes.

## Runtime Setup
- Default URL: `sqlite:///./petition_verifier.db`.
- Render/Postgres URLs beginning with `postgres://` are rewritten to `postgresql://`.
- `Base.metadata.create_all(engine)` creates missing tables at startup.
- A small list of best-effort `ALTER TABLE` statements runs at startup and swallows errors after rollback.
- There is no active Alembic migration workflow despite `alembic` being installed.

Treat schema changes as high risk. Document expected SQLite and Postgres behavior before changing columns or table names.

## Table Groups
| Area | Tables/classes | Used for |
| --- | --- | --- |
| Petition projects | `ProjectRow`, `SignatureRow` | classic PDF processing, signature lines, review/export |
| Users/auth/workforce | `UserRow`, `WorkerProjectRow`, `TeamRow` | login users, roles, team membership, project assignment |
| Shifts/schedule | `ShiftRow`, `ScheduleRequestRow`, `ShiftReflectionRow` | clock-in/out, schedule requests, shift reflections |
| Payroll/payment | `PayPeriodRow`, `PayrollRecordRow`, `PaymentPreferenceRow` | pay period records, payroll calculations, payment preferences |
| Field stats/location | `LiveSigCountRow`, `WorkerLocationRow` | live signature counts and map pins |
| Review center | `PacketRow`, `PacketLineRow` | uploaded packet files, OCR rows, voter/fraud review decisions |
| Settings | `AppSettingRow` | app-level settings such as field-manager password |

## Service Boundary
- Routes import the shared singleton as `from ..storage import db`.
- CLI and pipeline paths may instantiate `Database()` directly when saving processed projects.
- `Database` methods generally open a short-lived session per call and expunge returned ORM rows when needed.

## Editing Rules
- Add route behavior by calling existing `Database` methods when possible.
- If adding a DB method, keep it near related methods in `database.py` and return detached rows or plain dicts consistently with neighboring code.
- Do not silently add startup `ALTER TABLE` statements without documenting the intended SQLite/Postgres behavior.
- Avoid broad splits of `database.py` in small feature work; first add tests/docs around the behavior you are touching.

## Local State
Runtime DB files are ignored by git via `*.db`. If a local server smoke creates `petition_verifier.db`, remove it before finalizing:
```bash
rm -f petition_verifier.db
```
