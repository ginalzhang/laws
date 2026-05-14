# Route Map

Routes are split between legacy handlers in `src/petition_verifier/api.py` and modular routers in `src/petition_verifier/routes/`. Check both places before adding or changing an endpoint.

## App-Level Routes In `api.py`
| Route group | Purpose |
| --- | --- |
| `GET /health`, `/healthcheck-anthropic`, `/healthcheck-anthropic-full` | health and external API diagnostics |
| `GET /`, `/canvasser`, `/field-manager`, `/evann` | serve static HTML entry pages |
| `GET /app/review` | serve built React review queue when `web/dist` exists |
| `GET /stats/live-count` | public live approved-signature count |
| `GET /projects`, `/projects/{project_id}/signatures`, `/projects/{project_id}/signatures/{line_number}` | legacy project browsing |
| `POST /projects/{project_id}/signatures/{line_number}/review` | legacy staff review update |
| `POST /process`, `POST /projects/{project_id}/process` | classic petition processing |
| `POST /fraud-scan`, `GET /fraud-alerts` | fraud analysis and alerts |
| `POST /worker/upload`, `PATCH /worker/projects/{project_id}/count` | worker upload/count paths |
| `GET /projects/{project_id}/export`, `POST /projects/{project_id}/assign` | project export and assignment |
| `POST /seed-demo-data`, `POST /fix-*` | maintenance/demo repair endpoints |

## Mounted Routers
| Prefix | File | Notes |
| --- | --- | --- |
| `/auth` | `routes/auth_routes.py` | login, dev-token, scan-login, active users, FM helpers, current user |
| `/workers` | `routes/worker_routes.py` | worker CRUD, wage, assignments, manual sigs, activate/deactivate |
| `/shifts` | `routes/shift_routes.py` | clock-in/out, manual shifts, approval, notes, deletion |
| `/schedule` | `routes/schedule_routes.py` | schedule requests |
| `/payroll` | `routes/payroll_routes.py` | preview, records, periods, run, P&L |
| `/payment-preferences` | `routes/payment_routes.py` | payment preference get/update |
| `/locations` | `routes/location_routes.py` | worker map pins |
| `/stats` | `routes/stats_routes.py` | signature counts, locations, live stats, my count |
| `/review` | `routes/review_routes.py` | packet upload/review/image/voter/fraud/export flow |
| `/teams` | `routes/team_routes.py` | teams and team membership |
| `/reflections` | `routes/reflection_routes.py` | shift reflections |
| none | `routes/leaderboard_routes.py` | `/leaderboard` |

## Frontend Entry Points
- `ui/login.html`: calls `/auth/active-users`, `/auth/login-by-name`, `/auth/login`, `/auth/scan-login`, `/auth/fm-users`.
- `ui/dashboard.html`: calls `/auth/*`, `/projects`, `/projects/{id}/assign`, `/fraud-scan`, `/review/upload`, `/review/packets/{id}`.
- `ui/worker.html`: calls worker/auth APIs and `/worker/upload`.
- `ui/canvasser.html`: calls `/review/upload` and authenticated worker APIs.
- `ui/field-manager.html` and `ui/evann.html`: call stats/auth/workforce APIs.
- `ui/index.html`: legacy review UI calling `/process` and `/fraud-scan`.
- `web/`: React review queue milestone calling `/review/*`; it keeps current bearer-token compatibility and does not replace vanilla routes yet.

## Auth Notes
Current auth behavior is mixed. Some endpoints use bearer auth through `get_current_user`, some are public, and some maintenance/demo endpoints are unauthenticated. Treat auth changes as product/security changes, not cleanup.
