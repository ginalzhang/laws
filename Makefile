PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3.11)
HOST ?= 127.0.0.1
PORT ?= 8000
BASE_URL ?= http://$(HOST):$(PORT)
SECRET_KEY ?= dev-local-secret

.PHONY: setup compile test-fast check-system-deps fixtures test run smoke-local

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

run:
	SECRET_KEY=$(SECRET_KEY) PYTHONPATH=src $(PYTHON) -m uvicorn petition_verifier.api:app --host $(HOST) --port $(PORT)

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
