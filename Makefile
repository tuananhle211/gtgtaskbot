# MeoBot developer commands.
#
# Local quality targets run through uv on your machine.
# Compose targets run Docker; on the NAS run them from /volume1/docker/meobot.
#
# Safety: no target here removes a volume or prunes system resources.

SHELL := /bin/bash
COMPOSE ?= docker compose
COMPOSE_DEV := $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml
UV ?= uv

.DEFAULT_GOAL := help
.PHONY: help install lint format format-check typecheck test test-cov check \
        compose-config up up-dev down restart logs ps migrate migration \
        shell-api shell-db worker-ping

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# --- Local development ------------------------------------------------------
install: ## Sync the virtualenv from uv.lock (including dev tools)
	$(UV) sync --frozen

lint: ## Run Ruff lint checks
	$(UV) run ruff check .

format: ## Reformat the codebase with Ruff
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

format-check: ## Verify formatting without writing
	$(UV) run ruff format --check .

typecheck: ## Run mypy over src/
	$(UV) run mypy src

test: ## Run the test suite
	$(UV) run pytest

test-cov: ## Run tests with a coverage report
	$(UV) run pytest --cov --cov-report=term-missing

check: lint format-check typecheck test ## Everything CI would run

# --- Docker Compose ---------------------------------------------------------
compose-config: ## Validate and render the compose configuration
	$(COMPOSE) config

up: ## Build and start all services (detached)
	$(COMPOSE) up -d --build

up-dev: ## Start with the development overlay (source mounted, API reload)
	$(COMPOSE_DEV) up -d --build

down: ## Stop and remove containers. Volumes and data are PRESERVED.
	$(COMPOSE) down

restart: ## Restart every service
	$(COMPOSE) restart

ps: ## Show service status
	$(COMPOSE) ps

logs: ## Follow logs from all services
	$(COMPOSE) logs -f --tail=200

# --- Database ---------------------------------------------------------------
migrate: ## Apply all migrations (alembic upgrade head)
	$(COMPOSE) run --rm api alembic upgrade head

migration: ## Autogenerate a migration: make migration m="add scripts table"
	@test -n "$(m)" || (echo 'Usage: make migration m="describe the change"'; exit 1)
	$(COMPOSE) run --rm api alembic revision --autogenerate -m "$(m)"

# --- Shells & diagnostics ---------------------------------------------------
shell-api: ## Open a shell in a throwaway api container
	$(COMPOSE) run --rm api /bin/bash

shell-db: ## Open psql inside the postgres container
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-meobot} -d $${POSTGRES_DB:-meobot}

worker-ping: ## Ping Celery workers
	$(COMPOSE) exec worker celery -A meobot.tasks.celery_app inspect ping
