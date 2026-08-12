.PHONY: help check-python setup run find test clean clean-docker clean-data clean-venv embedder-build embedder-up embedder-down embedder-logs embedder-health up down

# Use uv if available, else fall back to the local venv's python.
PYTHON_BIN ?= python3
PYTHON := $(shell command -v uv >/dev/null 2>&1 && echo "uv run python" || echo ".venv/bin/python")
# Docker Compose v2 ships as a plugin (`docker compose`); v1 was a hyphenated
# binary (`docker-compose`). Detect whichever exists so this works on either.
COMPOSE := $(shell docker compose version >/dev/null 2>&1 && echo "docker compose" \
                       || (command -v docker-compose >/dev/null 2>&1 && echo "docker-compose"))

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# ── one-shot ─────────────────────────────────────────────
check-python: ## Verify the selected interpreter is Python 3.12+
	@if ! command -v "$(PYTHON_BIN)" >/dev/null 2>&1; then \
		echo "Error: Python interpreter '$(PYTHON_BIN)' was not found."; \
		echo "Install Python 3.12+ or run: make setup PYTHON_BIN=python3.12"; \
		exit 1; \
	fi
	@if ! "$(PYTHON_BIN)" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then \
		echo "Error: Python 3.12 or newer is required; detected $$($(PYTHON_BIN) --version 2>&1)."; \
		echo "Install a newer Python or run: make setup PYTHON_BIN=python3.12"; \
		exit 1; \
	fi
	@echo "$$($(PYTHON_BIN) --version) is supported."

setup: check-python ## Install Python deps + create .env from template
	@command -v uv >/dev/null 2>&1 && uv venv --python "$(PYTHON_BIN)" && uv pip install -r requirements.txt \
		|| ("$(PYTHON_BIN)" -m venv .venv && .venv/bin/pip install -r requirements.txt)
	@test -f .env || cp .env.example .env
	@echo "Setup done. Add resume.md/resume.pdf/cv.pdf, then 'make up'."

up: embedder-up ## Start the whole stack (embedder sidecar + web dashboard)
	@echo "Starting dashboard at http://127.0.0.1:8000 …"
	@echo "Stop everything with:  make down"
	$(PYTHON) server.py

down: embedder-down ## Stop the embedder sidecar (host server exits with Ctrl-C)
	@echo "Stack stopped."

# ── embedder sidecar ─────────────────────────────────────
embedder-build: ## Build the embedder docker image (first run downloads ~90MB model)
	@if [ -z "$(COMPOSE)" ]; then \
		echo "Error: docker compose not found. Install Docker Desktop or the compose plugin."; \
		exit 1; \
	fi
	$(COMPOSE) build embedder

embedder-up: embedder-build ## Build (if needed) + start the embedder sidecar in the background
	$(COMPOSE) up -d embedder
	@echo "Waiting for sidecar health…"
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
		if curl -sf http://localhost:8787/health >/dev/null 2>&1; then \
			echo "Embedder ready: $$(curl -s http://localhost:8787/health)"; exit 0; \
		fi; \
		echo "  …waiting for sidecar ($$i/10)"; sleep 3; \
	done; \
	echo "Warning: embedder did not become healthy in 30s — pipeline will fall back to keyword-only ranking."; \
	echo "Check logs with:  make embedder-logs"

embedder-down: ## Stop + remove the embedder container (cached model volume is preserved)
	@if [ -n "$(COMPOSE)" ]; then $(COMPOSE) stop embedder; fi

embedder-logs: ## Tail embedder logs (model load progress, errors)
	@if [ -n "$(COMPOSE)" ]; then $(COMPOSE) logs -f embedder; fi

embedder-health: ## Curl the sidecar /health endpoint
	@curl -s http://localhost:8787/health && echo "" || echo "sidecar not running"

# ── pipeline + dashboard ─────────────────────────────────
run: ## Start only the web dashboard (assumes sidecar is up or accepts fallback)
	$(PYTHON) server.py

find: ## Run the pipeline headless (no web UI)
	$(PYTHON) finder.py

# ── tests + cleanup ──────────────────────────────────────
test: ## Run the test suite
	$(PYTHON) -m pytest -q

# Clean is destructive: wipes all generated data + the venv + docker state.
# `.env`, `resume.md`, `resume.pdf`, `cv.*`, and `.git/` are preserved
# (those are user-owned, not generated).
clean: clean-docker clean-data clean-venv ## Wipe EVERYTHING: docker image + volume, output, embeddings cache, venv, pycache
	@echo ""
	@echo "✓ Project fully cleaned. Rebuild with:  make setup && make up"

clean-docker: ## Stop + remove the embedder container, image, and model volume
	@if [ -n "$(COMPOSE)" ]; then \
		echo "• Stopping + removing embedder container…"; \
		$(COMPOSE) down --remove-orphans --volumes embedder 2>/dev/null || true; \
		echo "• Removing docker image ai-job-finder-embedder…"; \
		docker rmi ai-job-finder-embedder 2>/dev/null || true; \
		echo "• Pruning dangling docker volumes (embedder-models)…"; \
		docker volume rm ai-job-finder_embedder-models 2>/dev/null || true; \
	else \
		echo "• docker compose not found — skipping docker cleanup"; \
	fi

clean-data: ## Remove output dir (jobs, raw jobs, embeddings, analysis cache, cover letters) + Python caches
	@echo "• Removing output/ (jobs, embeddings, analysis cache, cover letters)…"
	rm -rf output
	@echo "• Removing __pycache__ + .pytest_cache…"
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache

clean-venv: ## Remove the local virtualenv (forces full deps reinstall on next setup)
	@if [ -d .venv ]; then \
		echo "• Removing .venv/ ($(du -sh .venv 2>/dev/null | cut -f1))…"; \
		rm -rf .venv; \
	else \
		echo "• .venv/ not present — skipping"; \
	fi
