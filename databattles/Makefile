# Developer commands. Requires: Python 3.11+, Node 22+, PostgreSQL 16 (or `make db` with Docker).
PY ?= backend/.venv/bin/python
PIP ?= backend/.venv/bin/pip

.PHONY: help setup db migrate seed reset-demo api worker web dev test test-backend test-e2e lint typecheck build openapi up down

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

setup: ## Create the Python venv and install backend + web dependencies
	python3 -m venv backend/.venv
	$(PIP) install -r backend/requirements.lock -r backend/requirements-dev.txt
	cp -n backend/.env.example backend/.env || true
	cd web && npm ci && (cp -n .env.example .env.local || true)

db: ## Start a local PostgreSQL 16 in Docker (user/password/db: databattles)
	docker run -d --name databattles-db -p 5432:5432 -e POSTGRES_USER=databattles -e POSTGRES_PASSWORD=databattles \
	  -e POSTGRES_DB=databattles postgres:16-alpine
	@echo "Also create the test database: docker exec databattles-db createdb -U databattles databattles_test"

migrate: ## Apply database migrations
	cd backend && .venv/bin/alembic upgrade head

seed: ## Load synthetic demo data (idempotent)
	cd backend && .venv/bin/python -m app.seed

reset-demo: ## Remove and recreate demo data
	cd backend && .venv/bin/python -m app.seed --reset

api: ## Run the API on :8000 with reload
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

worker: ## Run the background worker (scoring, email, GitHub sync, reminders)
	cd backend && .venv/bin/python -m app.worker

web: ## Run the Next.js dev server on :3000
	cd web && npm run dev

test: test-backend ## Run backend tests (needs PostgreSQL with databattles_test)

test-backend:
	cd backend && .venv/bin/pytest

test-e2e: ## Run Playwright tests against a running, seeded stack
	cd web && npx playwright test

lint: ## Lint backend and web
	cd backend && .venv/bin/ruff check app tests
	cd web && npm run lint

typecheck: ## Type-check the web app
	cd web && npm run typecheck

build: ## Production build of the web app
	cd web && npm run build

openapi: ## Regenerate OpenAPI JSON and frontend types
	cd backend && .venv/bin/python -c "import json, app.main as m; json.dump(m.app.openapi(), open('openapi.json','w'), indent=1)"
	cd web && npm run gen:api

up: ## docker compose up (production-style single node)
	docker compose up -d --build

down:
	docker compose down
