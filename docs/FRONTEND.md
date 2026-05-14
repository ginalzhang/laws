# Frontend Map

The production frontend is still the plain static HTML/CSS/JS in `ui/`. FastAPI serves entry pages from `api.py` and static assets from `/static`.

A React/Vite milestone app now lives in `web/` for the review queue only. It is served at `/app/review` after `make web-build`, uses the existing bearer token in `localStorage`, and does not replace any vanilla page yet.

## Entry Pages
| URL | File | Primary role |
| --- | --- | --- |
| `/` | `ui/login.html` | role-aware login and redirect |
| `/canvasser` | `ui/canvasser.html` | canvasser field workflow and packet upload |
| `/field-manager` | `ui/field-manager.html` | field-manager workflow and stats |
| `/evann` | `ui/evann.html` | Evan/admin workflow |
| `/static/dashboard.html` | `ui/dashboard.html` | large management dashboard |
| `/static/worker.html` | `ui/worker.html` | worker PWA-style workflow |
| `/static/index.html` | `ui/index.html` | legacy petition review UI |
| `/app/review` | `web/` build output | React review queue milestone |

## State And Auth
- Auth tokens are stored in `localStorage` under `pv_token`.
- Common localStorage keys: `pv_token`, `pv_role`, `pv_user_id`, `pv_full_name`, and sometimes `pv_name`.
- Session-only UI counters and install state use `sessionStorage`, especially `pv_sig_count` and `pv_install_dismissed`.
- Most authenticated pages add `Authorization: Bearer <token>` manually inside local `api()` helpers.
- Logout usually clears `localStorage`; some pages also clear `sessionStorage`.

## Endpoint Coupling
- `login.html`: `/auth/active-users`, `/auth/login-by-name`, `/auth/login`, `/auth/scan-login`, `/auth/fm-users`.
- `dashboard.html`: `/auth/*`, `/projects`, `/projects/{id}/assign`, `/fraud-scan`, `/review/upload`, `/review/packets/{id}`.
- `worker.html`: worker/auth APIs plus `/worker/upload`.
- `canvasser.html`: authenticated worker APIs plus `/review/upload`.
- `field-manager.html` and `evann.html`: stats/auth/workforce APIs.
- `index.html`: legacy `/process` and `/fraud-scan` flows.
- `web/src/App.tsx`: `/review/packets`, `/review/counties`, packet image/export/action/voter/fraud endpoints.

Before changing a route path or response shape, search `ui/` for that endpoint and update every caller.

## React Review Queue
- Install/build/test with `make web-install`, `make web-build`, `make web-test`, and `make web-e2e`.
- `web/scripts/generate-api-types.mjs` imports FastAPI OpenAPI and writes `web/src/api/schema.d.ts`; run `npm run generate:api` after changing review route request/response models.
- Vite dev server proxies `/auth` and `/review` to `127.0.0.1:8000`.
- Cutover is intentionally not active. To cut over later, point the dashboard Review Center entry to `/app/review` after parity sign-off.
- Rollback is removing that link or route change; the vanilla `ui/dashboard.html` review center remains untouched.

## Large-File Guidance
- `dashboard.html` is the riskiest frontend file; it combines login, project assignment, fraud scan, packet upload, local roster state, and rendering logic.
- Prefer narrow edits near the relevant fetch/render function.
- Use `web/` only for the React review queue milestone; do not move unrelated vanilla surfaces into React in the same change.
- If a change touches repeated auth/fetch behavior across pages, document the duplicated callers instead of attempting a broad refactor unless the task explicitly asks for it.

## Quick Checks
```bash
make run
make smoke-local
```

Manual browser smoke paths:
- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/canvasser`
- `http://127.0.0.1:8000/field-manager`
- `http://127.0.0.1:8000/evann`
- `http://127.0.0.1:8000/static/dashboard.html`
