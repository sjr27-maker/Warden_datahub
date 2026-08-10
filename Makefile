.PHONY: setup test test-live lint fmt datahub-up datahub-down demo clean \
        build-world clean-world ingest-dark ingest-covered datahub-reset demo-dark demo-covered \
		snapshot-dark snapshot-covered

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
COMPOSE := $(HOME)/datahub-auth-compose.yml

ifneq (,$(wildcard .env))
include .env
export
endif

DBT := cd world/dbt_project && DBT_PROFILES_DIR=. $(abspath $(VENV))/bin/dbt

setup:
	python3.11 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt
	$(PIP) install -e .

test:
	$(VENV)/bin/pytest -m "not live"

test-live:
	$(VENV)/bin/pytest -m live

lint:
	$(VENV)/bin/ruff check warden tests
	$(VENV)/bin/ruff format --check warden tests

fmt:
	$(VENV)/bin/ruff check --fix warden tests
	$(VENV)/bin/ruff format warden tests

datahub-up:
	$(VENV)/bin/datahub docker quickstart -f $(COMPOSE)

datahub-down:
	$(VENV)/bin/datahub docker quickstart --stop

build-world:
	$(PY) world/generate_data.py
	$(DBT) build
	$(PY) world/transforms/customer_segments.py

clean-world:
	rm -f world/warehouse.duckdb
	rm -rf world/dbt_project/target world/dbt_project/logs

ingest-dark:
	$(PY) -m warden.ingest --profile dark

ingest-covered:
	$(PY) -m warden.ingest --profile covered

demo: build-world ingest-dark
	@echo "world built and dark profile ingested"

clean: clean-world
	find . -type d -name __pycache__ -exec rm -rf {} +

datahub-reset:
	$(VENV)/bin/datahub docker nuke
	$(VENV)/bin/datahub docker quickstart -f $(COMPOSE)
	@echo ""
	@echo "  DataHub was nuked — the previous access token is gone."
	@echo "  Generate a new one at http://localhost:9002 (Settings > Access Tokens),"
	@echo "  put it in .env, then run:  make ingest-dark   (or make ingest-covered)"
	@echo ""

demo-dark: datahub-reset build-world
demo-covered: datahub-reset build-world

snapshot-dark:
	$(PY) -m warden.capture_snapshot --profile dark

snapshot-covered:
	$(PY) -m warden.capture_snapshot --profile covered