.PHONY: setup test lint fmt datahub-up datahub-down demo clean

VENV := .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
COMPOSE := $(HOME)/datahub-auth-compose.yml

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

demo:
	@echo "not implemented until batch 3"

clean:
	rm -rf world/dbt_project/target world/dbt_project/logs *.duckdb
	find . -type d -name __pycache__ -exec rm -rf {} +