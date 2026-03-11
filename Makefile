# ============================================================================
# Dubora Monorepo - Makefile
# ============================================================================
#
# Package layout:
#   packages/core/     → dubora-core (shared data layer)
#   packages/pipeline/ → dubora-pipeline (heavy execution)
#   packages/web/      → dubora-web (FastAPI server)
#
# ============================================================================

VENV := .venv
PIP  := $(VENV)/bin/pip
PY   := $(VENV)/bin/python

.PHONY: help clean install-core install-pipeline install-web install-all install-dev test lint

help:
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "Dubora Monorepo"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "Install:"
	@echo "  make install-core      - Install dubora-core (shared data layer)"
	@echo "  make install-pipeline  - Install dubora-core + dubora-pipeline"
	@echo "  make install-web       - Install dubora-core + dubora-web"
	@echo "  make install-all       - Install all three packages (editable)"
	@echo "  make install-dev       - Dev tools (pytest, ruff)"
	@echo ""
	@echo "Dev:"
	@echo "  make test              - Run tests"
	@echo "  make lint              - Code check (ruff)"
	@echo "  make clean             - Remove caches"
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ============================================================================
# Clean
# ============================================================================

clean:
	@echo "Cleaning caches..."
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@find . -name "*.pyc" -delete 2>/dev/null || true
	@find . -name "*.pyo" -delete 2>/dev/null || true
	@find . -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	@find . -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
	@echo "Done"

# ============================================================================
# Install targets
# ============================================================================

install-core:
	@echo "Installing dubora-core..."
	@$(PIP) install -e packages/core

install-pipeline: install-core
	@echo "Installing dubora-pipeline..."
	@$(PIP) install -e packages/pipeline

install-web: install-core
	@echo "Installing dubora-web..."
	@$(PIP) install -e packages/web

install-all: install-core
	@echo "Installing all packages..."
	@$(PIP) install -e packages/pipeline -e packages/web

install-dev:
	@echo "Installing dev tools..."
	@$(PIP) install -e ".[dev]"

# ============================================================================
# Dev commands
# ============================================================================

test:
	@echo "Running tests..."
	@$(PY) -m pytest test/ -v

lint:
	@echo "Code check (ruff)..."
	@$(VENV)/bin/ruff check packages/
	@echo "Done"
