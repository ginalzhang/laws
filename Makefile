PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3.11)
HOST ?= 127.0.0.1
PORT ?= 8000
BASE_URL ?= http://$(HOST):$(PORT)
SECRET_KEY ?= dev-local-secret
DATABASE_URL ?= sqlite:///./petition_verifier.db
DEV_AUTO_LOGIN ?= true

.PHONY: setup compile test-fast check-system-deps fixtures test db-upgrade db-sql web-install web-generate-api web-build web-test web-e2e run smoke-local

setup:
	$(PYTHON) -m pip install --upgrade pip
	$(PYTHON) -m pip install -e '.[dev]'

compile:
	$(PYTHON) -m compileall -q src tests

test-fast:
	$(PYTHON) -m pytest tests/test_matching.py tests/test_extraction_ensemble.py tests/test_app_smoke.py -v

check-system-deps:
	@command -v pdfinfo >/dev/null || (echo "Missing pdfinfo. Install Poppler: brew install poppler" >&2; exit 1)
	@command -v tesseract >/dev/null || (echo "Missing tesseract. Install Tesseract: brew install tesseract" >&2; exit 1)

fixtures:
	$(PYTHON) tests/fixtures/generate_test_data.py

test: check-system-deps
	$(PYTHON) -m pytest tests/ -v

db-upgrade:
	DATABASE_URL=$(DATABASE_URL) PYTHONPATH=src $(PYTHON) -m alembic upgrade head

db-sql:
	DATABASE_URL=$(DATABASE_URL) PYTHONPATH=src $(PYTHON) -m alembic upgrade head --sql

web-install:
	cd web && npm install

web-generate-api:
	cd web && npm run generate:api

web-build:
	cd web && npm run build

web-test:
	cd web && npm run typecheck && npm run lint && npm run test

web-e2e:
	cd web && npm run test:e2e

run:
	DATABASE_URL=$(DATABASE_URL) PYTHONPATH=src $(PYTHON) -m alembic upgrade head
	DATABASE_URL=$(DATABASE_URL) SECRET_KEY=$(SECRET_KEY) DEV_AUTO_LOGIN=$(DEV_AUTO_LOGIN) PYTHONPATH=src $(PYTHON) -m uvicorn petition_verifier.api:app --host $(HOST) --port $(PORT)

smoke-local:
	curl -sf "$(BASE_URL)/health"
	curl -sf "$(BASE_URL)/auth/active-users"
	TOKEN=$$(curl -sf -X POST "$(BASE_URL)/auth/login" \
		-H "Content-Type: application/json" \
		-d '{"email":"arianafan2000@app.local","password":"arianafan2000"}' \
		| $(PYTHON) -c "import sys,json; print(json.load(sys.stdin)['access_token'])"); \
	curl -sf "$(BASE_URL)/review/packets" -H "Authorization: Bearer $$TOKEN"; \
	curl -sf "$(BASE_URL)/projects" -H "Authorization: Bearer $$TOKEN"; \
	echo "Local smoke tests passed"
