.PHONY: help up down install test demo experiments ci-local lint clean

PY := .venv/bin/python

help:
	@echo "make demo         one command: broker up, small run, negative controls"
	@echo "make experiments  full measured matrix (10k events, ~3 min)"
	@echo "make test         unit tests (no broker needed)"
	@echo "make ci-local     what CI runs: tests + full matrix + assertions"
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
	$(PY) -m scp.experiments --trajectories 60 --steps 10 --out results/demo

experiments: up
	$(PY) -m scp.experiments --trajectories 500 --steps 20 | tee results/full_run.txt

# Mirrors .github/workflows/ci.yml as closely as a local machine can. The one thing it
# cannot mirror is the absence of omega_seal on a clean runner; see docs/CI.md.
ci-local: up
	$(PY) -m pytest -q
	$(PY) -m scp.experiments --trajectories 500 --steps 20 | tee results/full_run.txt
	$(PY) .github/assert_results.py

lint:
	.venv/bin/ruff check src tests

clean:
	rm -rf results .pytest_cache .ruff_cache __pycache__
