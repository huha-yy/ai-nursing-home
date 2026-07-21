SHELL := /bin/bash
COMPOSE := docker compose --project-name dato --project-directory infra --env-file install/dato-ota-defaults.env --env-file infra/.env

unexport PROD_URL

SQ := '
SQ_ESC := '\''

.PHONY: help up down restart logs ps psql redis-cli lint fmt test smoke clean clean-images build githooks-install _ensure-control-secrets control-test control-bootstrap admin-init control-logs cognee-logs gbrain-logs reprovision-tier1 init reset wipe wipe-all

help:
	@echo "  --- Operator targets ---"
	@echo "make init        - first install (non-interactive, idempotent)"
	@echo "make up          - start the foundation stack"
	@echo "make down        - stop and remove containers"
	@echo "make restart     - down then up"
	@echo "make reset       - non-destructive in-place re-init"
	@echo "make wipe        - destructive factory reset (WIPE_ARGS for extra flags)"
	@echo "make wipe-all    - wipe everything including OTA test containers"
	@echo "make logs        - tail logs from all services"
	@echo "make ps          - show running services"
	@echo "make psql        - open psql against dato-postgres"
	@echo "make redis-cli   - open redis-cli against dato-redis"
	@echo "make smoke       - bring stack up, run smoke tests, leave running"
	@echo ""
	@echo "  --- Developer targets ---"
	@echo "make lint        - run ruff check"
	@echo "make fmt         - run ruff format"
	@echo "make test        - run pytest (no compose required)"
	@echo "make clean       - down + prune project volumes"
	@echo "make clean-images - clean + remove project-built images"
	@echo "make build       - build all service images"
	@echo "make control-test      - run the dl-control unit/integration suite"
	@echo "make admin-init   - create the first admin user (idempotent)"
	@echo "make control-logs      - tail dl-control logs"
	@echo "make cognee-logs       - tail dl-cognee logs"
	@echo "make gbrain-logs       - tail dl-gbrain logs"
	@echo "make githooks-install - activate the Codex pre-commit gate"

up:
	scripts/init --no-start
	$(COMPOSE) up -d
	scripts/wait-for-stack

init:
	scripts/init

reset:
	scripts/reset

wipe:
	scripts/wipe $(WIPE_ARGS)

wipe-all:
	scripts/wipe --all

down:
	$(COMPOSE) down

restart:
	$(MAKE) down
	$(MAKE) up

logs:
	$(COMPOSE) logs -f --tail=100

ps:
	$(COMPOSE) ps

psql:
	$(COMPOSE) exec dato-postgres psql -U dato -d dato

redis-cli:
	$(COMPOSE) exec dato-redis redis-cli

lint:
	PYTHONPATH= uv run ruff check . && cd dl-control && uv run ruff check .

fmt:
	PYTHONPATH= uv run ruff format .

test:
	PYTHONPATH= uv run pytest tests/ -v
	$(MAKE) control-test

smoke:
	$(MAKE) up
	$(MAKE) admin-init
	@POSTGRES_PASSWORD=$$(grep '^POSTGRES_PASSWORD=' infra/.env | cut -d= -f2-) \
	 PYTHONPATH= uv run pytest tests/test_smoke.py tests/test_smoke_p13_peer.py -v

clean:
	$(COMPOSE) down -v

clean-images:
	$(COMPOSE) down -v --rmi local

build: _ensure-control-secrets
	DOCKER_BUILDKIT=0 $(COMPOSE) build \
		dato-caddy dato-control-migrate dato-control \
		dl-cognee dl-cognee-reranker dl-llm-local dl-llm-proxy dl-gbrain

githooks-install:
	git config core.hooksPath .githooks
	@echo "Pre-commit hook activated. Run 'git config --unset core.hooksPath' to deactivate."

_ensure-control-secrets:
	@test -f infra/.env || cp infra/.env.example infra/.env
	@grep -q '^DL_CONTROL_SECRET_KEY=' infra/.env || \
	  echo "DL_CONTROL_SECRET_KEY=$$(openssl rand -base64 24)" >> infra/.env
	@grep -q '^DL_CONTROL_APP_PASSWORD=' infra/.env || \
	  echo "DL_CONTROL_APP_PASSWORD=$$(openssl rand -hex 16)" >> infra/.env
	@grep -q '^DL_COGNEE_ADMIN_TOKEN=' infra/.env || \
	  echo "DL_COGNEE_ADMIN_TOKEN=$$(openssl rand -hex 32)" >> infra/.env
	@grep -q '^DL_INTERNAL_API_KEY=' infra/.env || \
	  echo "DL_INTERNAL_API_KEY=$$(openssl rand -hex 32)" >> infra/.env
	@grep -q '^DL_COGNEE_PG_PASSWORD=' infra/.env || \
	  echo "DL_COGNEE_PG_PASSWORD=$$(openssl rand -hex 16)" >> infra/.env
	@grep -q '^DL_GBRAIN_PG_PASSWORD=' infra/.env || \
	  echo "DL_GBRAIN_PG_PASSWORD=$$(openssl rand -hex 16)" >> infra/.env

control-test:
	cd dl-control && uv run pytest -q

control-bootstrap: admin-init

admin-init:
	$(COMPOSE) run --rm dato-control python -m dl_control.bootstrap

control-logs:
	$(COMPOSE) logs -f --tail=100 dato-control

cognee-logs:
	$(COMPOSE) logs -f --tail=100 dl-cognee

gbrain-logs:
	$(COMPOSE) logs -f --tail=100 dl-gbrain

reprovision-tier1:
	$(COMPOSE) exec -T dato-control python -m dl_control.agents.cli_reprovision

smoke-local-llm:
	PYTHONPATH= uv run pytest tests/test_smoke_p6_llm.py -v

