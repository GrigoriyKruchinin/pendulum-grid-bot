COMPOSE_LOCAL := docker compose -f docker-compose.local.yml
UV := uv run

.PHONY: install lint format typecheck test test-integration check up down logs ps migrate data-load backtest walk-forward scan paper

install:
	uv sync

lint:
	$(UV) ruff check .
	$(UV) ruff format --check .

format:
	$(UV) ruff format .
	$(UV) ruff check --fix .

typecheck:
	$(UV) mypy src tests

test:
	$(UV) pytest -q

test-integration:
	$(UV) pytest -q -m integration

check: lint typecheck test

up:
	$(COMPOSE_LOCAL) up -d --build

down:
	$(COMPOSE_LOCAL) down

logs:
	$(COMPOSE_LOCAL) logs -f --tail=100

ps:
	$(COMPOSE_LOCAL) ps

migrate:
	$(COMPOSE_LOCAL) run --rm migrations

data-load:
	$(COMPOSE_LOCAL) run --rm tools pendulum data load $(ARGS)

backtest:
	$(COMPOSE_LOCAL) run --rm tools pendulum backtest run $(ARGS)

walk-forward:
	$(COMPOSE_LOCAL) run --rm tools pendulum backtest walk-forward $(ARGS)

scan:
	$(COMPOSE_LOCAL) run --rm tools pendulum scan run $(ARGS)

paper:
	$(COMPOSE_LOCAL) up -d paper-trader
