.PHONY: help up down install test demo experiments lint clean

PY := .venv/bin/python

help:
	@echo "make demo         one command: broker up, small run, negative controls"
	@echo "make experiments  full measured matrix (10k events, ~3 min)"
	@echo "make test         unit tests (no broker needed)"
	@echo "make up / down    start / stop the Redis broker"

install:
	uv venv --python 3.13 .venv
	uv pip install --python $(PY) -e ".[dev]"
	-uv pip install --python $(PY) -e $(HOME)/omega-seal

up:
	docker compose up -d
	@printf 'waiting for broker'; \
	for i in $$(seq 1 30); do \
	  docker exec scp-redis redis-cli ping >/dev/null 2>&1 && { echo ' ready'; exit 0; }; \
	  printf '.'; sleep 1; \
	done; echo ' TIMEOUT'; exit 1

down:
	docker compose down

test:
	$(PY) -m pytest -q

demo: up
	@echo
	$(PY) -m scp.experiments --trajectories 60 --steps 10

experiments: up
	$(PY) -m scp.experiments --trajectories 500 --steps 20 | tee results/full_run.txt

lint:
	.venv/bin/ruff check src tests

clean:
	rm -rf results .pytest_cache .ruff_cache __pycache__
