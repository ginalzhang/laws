release: PYTHONPATH=src python -m petition_verifier.cli.main db upgrade
web: uvicorn petition_verifier.api:app --host 0.0.0.0 --port $PORT
