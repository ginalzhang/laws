# Testing And Verification

Use Python 3.11. The repo has `.python-version` set to 3.11.9, CI uses 3.11, and macOS system Python 3.9 is not a supported runtime for this app.

## Fresh Local Setup
```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e '.[dev]'
```

Install system dependencies before OCR/PDF tests:
```bash
brew install tesseract poppler
```

For Google Vision or field-vision OCR work:
```bash
pip install -e '.[dev,vision]'
```

## Fast Checks
```bash
python -m compileall -q src tests
python -m pytest tests/test_matching.py tests/test_extraction_ensemble.py tests/test_app_smoke.py -v
```

Equivalent Make targets:
```bash
make compile
make test-fast
```
Make uses `.venv/bin/python` automatically when the venv exists; otherwise it falls back to `python3.11`.

## Full Tests
```bash
python -m pytest tests/ -v
```

Equivalent:
```bash
make check-system-deps
make test
```

The PDF and voter-roll fixtures are committed. Regenerate them only when intentionally updating fixtures:
```bash
make fixtures
```

## Local Server
```bash
SECRET_KEY=dev-local-secret PYTHONPATH=src python -m uvicorn petition_verifier.api:app --host 0.0.0.0 --port 8000
```

Equivalent:
```bash
make run
```

## Local Smoke Test
Start the server in one shell, then run:
```bash
make smoke-local
```

The smoke target mirrors CI:
- `GET /health`
- `GET /auth/active-users`
- login as the startup demo boss account
- `GET /review/packets`
- `GET /projects`

## CI Equivalent
CI installs system packages, installs `pip install -e '.[dev]'`, runs pytest, starts FastAPI, then runs curl smoke tests.

Local approximation:
```bash
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
python -m pytest tests/ -v
SECRET_KEY=ci-test-secret-key PYTHONPATH=src python -m uvicorn petition_verifier.api:app --host 0.0.0.0 --port 8000
```

## Known Gaps
- There is no lint or typecheck configuration yet.
- Route/auth coverage is mostly smoke-level.
- UI behavior is not covered by browser tests.
- Render still uses `requirements-deploy.txt`, so deploy dependency changes need a deploy-specific check.
