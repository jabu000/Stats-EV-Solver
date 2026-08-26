.PHONY: help setup dev api web build test fixtures seed clean

help:
	@echo "make setup     - create the venv and install backend + frontend deps"
	@echo "make dev       - run the API and the Vite dev server together"
	@echo "make api       - run just the API on :8000"
	@echo "make web       - run just the frontend dev server on :5173"
	@echo "make build     - build the frontend into frontend/dist (served by the API)"
	@echo "make test      - run the test suite"
	@echo "make fixtures  - regenerate the offline sample slates"
	@echo "make seed      - seed a demo graded history for the Track Record tab"
	@echo "make clean     - remove the local database and build output"

PY := .venv/bin/python
PIP := .venv/bin/pip

setup:
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"
	cd frontend && npm install
	@test -f .env || cp .env.example .env
	@echo "Setup complete. Run 'make build && make api', then open http://127.0.0.1:8000"

api:
	PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

web:
	cd frontend && npm run dev

dev:
	@echo "Starting API on :8000 and frontend on :5173 (Ctrl-C stops both)"
	@PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 & \
	cd frontend && npm run dev; kill %1 2>/dev/null || true

build:
	cd frontend && npm run build

test:
	PYTHONPATH=backend $(PY) -m pytest backend/tests -q

fixtures:
	$(PY) backend/fixtures/generate.py

seed:
	PYTHONPATH=backend $(PY) backend/fixtures/seed_history.py

clean:
	rm -f data/solver.db
	rm -rf frontend/dist
