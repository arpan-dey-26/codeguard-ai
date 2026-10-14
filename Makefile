# CodeGuard AI — development commands.
#
# Every command in the README is also a make target, so the documented way and
# the convenient way are the same thing and cannot drift apart.

.PHONY: help install install-backend install-frontend db-up db-down \
        migrate migrate-down api worker web test lint typecheck format \
        replay replay-sync replay-bad-signature build verify clean

BACKEND := backend
FRONTEND := frontend
PY := $(BACKEND)/.venv/bin/python
PIP := $(BACKEND)/.venv/bin/pip

help: ## Show available commands
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# --- setup -------------------------------------------------------------------
install: install-backend install-frontend ## Install all dependencies

install-backend: ## Create the backend venv and install dependencies
	cd $(BACKEND) && python3.12 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e ".[dev]"

install-frontend: ## Install frontend dependencies
	cd $(FRONTEND) && npm install

# --- database ----------------------------------------------------------------
db-up: ## Start PostgreSQL only
	docker compose up -d db

db-down: ## Stop all services
	docker compose down

migrate: ## Apply migrations
	cd $(BACKEND) && .venv/bin/alembic upgrade head

migrate-down: ## Roll back one migration
	cd $(BACKEND) && .venv/bin/alembic downgrade -1

# --- run ---------------------------------------------------------------------
api: ## Run the API with reload
	cd $(BACKEND) && .venv/bin/uvicorn app.main:app --reload --port 8000

worker: ## Run the background worker
	cd $(BACKEND) && .venv/bin/python -m app.worker

web: ## Run the Vite dev server
	cd $(FRONTEND) && npm run dev

# --- webhook replay (offline development and demo) ----------------------------
replay: ## Replay a sample pull_request.opened delivery at the local API
	GITHUB_WEBHOOK_SECRET=$${GITHUB_WEBHOOK_SECRET:?set it in your shell or .env} \
	python3 infrastructure/scripts/replay_webhook.py \
		--payload infrastructure/scripts/sample_payloads/pull_request_opened.json

replay-sync: ## Replay a pull_request.synchronize delivery
	GITHUB_WEBHOOK_SECRET=$${GITHUB_WEBHOOK_SECRET:?set it in your shell or .env} \
	python3 infrastructure/scripts/replay_webhook.py \
		--payload infrastructure/scripts/sample_payloads/pull_request_synchronize.json

replay-bad-signature: ## Prove the endpoint rejects a tampered delivery
	GITHUB_WEBHOOK_SECRET=$${GITHUB_WEBHOOK_SECRET:?set it in your shell or .env} \
	python3 infrastructure/scripts/replay_webhook.py --corrupt-signature \
		--payload infrastructure/scripts/sample_payloads/pull_request_opened.json

# --- quality -----------------------------------------------------------------
test: ## Run backend tests
	cd $(BACKEND) && .venv/bin/pytest

lint: ## Lint backend and frontend
	cd $(BACKEND) && .venv/bin/ruff check app tests && .venv/bin/ruff format --check app tests
	cd $(FRONTEND) && npm run lint

typecheck: ## Type check backend and frontend
	cd $(BACKEND) && .venv/bin/mypy app
	cd $(FRONTEND) && npm run typecheck

format: ## Auto-format backend and frontend
	cd $(BACKEND) && .venv/bin/ruff format app tests && .venv/bin/ruff check --fix app tests
	cd $(FRONTEND) && npm run format

build: ## Build the frontend for production
	cd $(FRONTEND) && npm run build

verify: lint typecheck test build ## Run every check CI runs
	@echo "All checks passed."

verify-db: ## Run the PostgreSQL verification harnesses (170 checks, needs a real PostgreSQL 16)
	cd $(BACKEND) && python3 verification/run_all.py

benchmark: ## Local timings for the CPU-bound stages. NOT an SLA measurement.
	cd $(BACKEND) && python3 verification/benchmark.py

clean: ## Remove build artefacts and caches
	rm -rf $(BACKEND)/.venv $(BACKEND)/.pytest_cache $(BACKEND)/.mypy_cache $(BACKEND)/.ruff_cache
	rm -rf $(FRONTEND)/node_modules $(FRONTEND)/dist
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
