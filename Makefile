.PHONY: setup test test-live lint fmt \
        datahub-up datahub-down datahub-reset \
        build-world clean-world \
        ingest-dark ingest-covered \
        snapshot-dark snapshot-covered \
        demo-dark demo-covered demo-offline run-live \
        clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
COMPOSE := $(HOME)/datahub-auth-compose.yml

ifneq (,$(wildcard .env))
include .env
export
endif

DBT := cd world/dbt_project && DBT_PROFILES_DIR=. $(abspath $(VENV))/bin/dbt
SCENARIO := world/scenarios/rename_cust_id.diff

# ---- setup ----

setup:
	python3.11 -m venv $(VENV)
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements-dev.txt
	$(PIP) install -e .

# ---- quality ----

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

# ---- datahub lifecycle ----

datahub-up:
	$(VENV)/bin/datahub docker quickstart -f $(COMPOSE)

datahub-down:
	$(VENV)/bin/datahub docker quickstart --stop

datahub-reset:
	$(VENV)/bin/datahub docker nuke
	$(VENV)/bin/datahub docker quickstart -f $(COMPOSE)
	@echo ""
	@echo "  DataHub was nuked — the previous access token is gone."
	@echo "  Generate a new one at http://localhost:9002 (Settings > Access Tokens),"
	@echo "  put it in .env, then run:"
	@echo "    set -a && source .env && set +a"
	@echo "    make ingest-dark        (or make ingest-covered)"
	@echo ""

# ---- world ----

build-world:
	$(PY) world/generate_data.py
	$(DBT) build
	$(PY) world/transforms/customer_segments.py

clean-world:
	rm -f world/warehouse.duckdb
	rm -rf world/dbt_project/target world/dbt_project/logs

# ---- ingestion ----

ingest-dark:
	$(PY) -m warden.ingest --profile dark

ingest-covered:
	$(PY) -m warden.ingest --profile covered

# ---- snapshots for the offline demo ----

snapshot-dark:
	$(PY) -m warden.capture_snapshot --profile dark 2>/dev/null

snapshot-covered:
	$(PY) -m warden.capture_snapshot --profile covered 2>/dev/null

# ---- demos ----

# Requires no Docker, no DataHub, no token. Replays committed snapshots.
demo-offline:
	$(PY) -m warden.demo_offline

# Full live run against whichever profile is currently ingested.
run-live:
	$(PY) -m warden.agent.run --diff $(SCENARIO) --profile live

# Reset to a clean profile. Stops after the reset — a new token is needed
# before ingestion can run.
demo-dark: datahub-reset build-world

demo-covered: datahub-reset build-world

# ---- housekeeping ----

clean: clean-world
	find . -type d -name __pycache__ -exec rm -rf {} +