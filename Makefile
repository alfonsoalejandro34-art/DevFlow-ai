# First Python >= 3.12 found: uv-managed interpreters, then python3.14..3.12 on PATH.
PY ?= $(firstword $(wildcard $(HOME)/.local/share/uv/python/cpython-3.1[2-9]*/bin/python3.1[2-9]) \
        $(shell command -v python3.14 python3.13 python3.12 2>/dev/null))
# Docker Desktop's per-user install puts the CLI here without adding it to PATH.
export PATH := $(HOME)/.docker/bin:$(PATH)
VENV := .venv
BIN := $(VENV)/bin
DEVFLOW := $(BIN)/devflow
RUNNER_IMAGE ?= devflow-runner:0.1
PIDS := .devflow/pids

.PHONY: help setup runner-image dev serve stop token doctor test test-backend test-docker test-frontend \
        lint typecheck format security check demo demo-local benchmark clean

help:  ## Show available targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

setup:  ## Create the virtualenv and install backend + frontend dependencies
	test -x $(BIN)/python || $(PY) -m venv $(VENV)
	$(BIN)/pip install -q --upgrade pip
	$(BIN)/pip install -q -e '.[dev]'
	cd frontend && npm ci

runner-image:  ## Build the sandbox image used for test runs
	docker build -t $(RUNNER_IMAGE) runner/

dev:  ## Run backend (auto-reload) and Vite dev server on localhost
	@mkdir -p $(PIDS)
	$(DEVFLOW) serve --reload & echo $$! > $(PIDS)/backend
	cd frontend && npx vite --host 127.0.0.1 & echo $$! > $(PIDS)/frontend
	@sleep 2; echo "Dashboard: http://127.0.0.1:5173   API: http://127.0.0.1:8000/docs   Token: $$($(DEVFLOW) token)"

serve:  ## Build the dashboard and serve everything from the backend on :8000
	cd frontend && npm run build
	$(DEVFLOW) serve

stop:  ## Stop servers started by `make dev`
	-@for f in $(PIDS)/*; do kill $$(cat $$f) 2>/dev/null; rm -f $$f; done
	-@pkill -f "devflow serve" 2>/dev/null; pkill -f "vite --host 127.0.0.1" 2>/dev/null; true

token:  ## Print the local API token
	@$(DEVFLOW) token

doctor:  ## Check local tools (Python, git, Docker, runner image, AI CLIs)
	@$(DEVFLOW) doctor

test: test-backend test-frontend  ## Run all non-Docker tests

test-backend:  ## Backend unit, API, security, and workflow tests
	$(BIN)/pytest -m "not docker and not ai_live" --cov --cov-report=term-missing:skip-covered

test-docker:  ## Docker sandbox tests (requires Docker + runner image)
	$(BIN)/pytest -m docker -v

test-frontend:  ## Frontend component tests
	cd frontend && npx vitest run

lint:  ## Ruff lint + format check, TypeScript typecheck
	$(BIN)/ruff check backend benchmarks scripts
	$(BIN)/ruff format --check backend benchmarks scripts
	cd frontend && npx tsc -b --noEmit

typecheck:  ## mypy (strict)
	$(BIN)/mypy

format:  ## Auto-format Python
	$(BIN)/ruff format backend benchmarks scripts
	$(BIN)/ruff check --fix backend benchmarks scripts

security:  ## Bandit, pip-audit, npm audit, workflow schema validation
	$(BIN)/bandit -q -c pyproject.toml -r backend/devflow
	$(BIN)/pip-audit --skip-editable --progress-spinner off
	cd frontend && npm audit --audit-level=moderate
	$(BIN)/check-jsonschema --builtin-schema vendor.github-workflows .github/workflows/*.yml

check: lint typecheck test security  ## Everything CI runs

demo:  ## Run the end-to-end demonstration against a running server
	$(BIN)/python scripts/demo.py

benchmark:  ## Run the benchmark suite against a running server
	$(BIN)/python benchmarks/run_benchmarks.py

clean:  ## Remove build output and caches (keeps .devflow data)
	rm -rf frontend/dist .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
