.PHONY: install dev infra-up infra-down infra-status api worker migrate test lint type-check check frontend-install frontend-dev frontend-check

install:
	uv sync --all-extras

dev:
	docker compose --profile application up --build

infra-up:
	docker compose --env-file docker/.env up -d postgres

infra-down:
	docker compose --env-file docker/.env stop postgres

infra-status:
	docker compose --env-file docker/.env ps postgres

api:
	uv run uvicorn village_insight.api.app:app --reload

worker:
	uv run village-insight-worker

migrate:
	uv run alembic upgrade head

test:
	uv run pytest

lint:
	uv run ruff check .

type-check:
	uv run mypy src

frontend-install:
	cd frontend && npm install

frontend-dev:
	cd frontend && npm run dev

frontend-check:
	cd frontend && npm run type-check && npm run test && npm run build

check: lint type-check test frontend-check
