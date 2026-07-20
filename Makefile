.PHONY: help setup run find test clean

# Use uv if available, else fall back to the local venv's python.
PYTHON := $(shell command -v uv >/dev/null 2>&1 && echo "uv run python" || echo ".venv/bin/python")

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Install dependencies into a virtualenv
	@command -v uv >/dev/null 2>&1 && uv venv && uv pip install -r requirements.txt \
		|| (python3 -m venv .venv && .venv/bin/pip install -r requirements.txt)
	@test -f .env || cp .env.example .env
	@echo "Setup done. Add your resume.md, then 'make run'."

run: ## Start the web dashboard (http://127.0.0.1:8000)
	$(PYTHON) server.py

find: ## Run the pipeline headless (no web UI)
	$(PYTHON) finder.py

test: ## Run the test suite
	$(PYTHON) -m pytest -q

clean: ## Remove generated output and caches
	rm -rf output .pytest_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
