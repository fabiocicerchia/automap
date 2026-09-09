# Subcommand for `make run`: map, check, adr, types, journeys, rules, langs
ARGS ?= map .

# Every verb this repository exposes lives here; `make` on its own prints them.
# FC-GEN-057: the same eight verbs in every repo, each either wired or a
# declared no-op that says why. None of them exit 0 quietly.

.DEFAULT_GOAL := help

.PHONY: help setup install build run test lint format analyze

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

run: ## Run automap on this checkout (ARGS="map ." by default)
	python3 automap.py $(ARGS)

test: ## Run the tests
	python3 tests/test_automap.py

# The tool pointed at itself: `check` compares the architecture it derives now
# against the committed baseline and exits 1 when they differ.
analyze: ## Fail if this repo's architecture has drifted from its baseline
	python3 automap.py check .

# --- Declared no-ops (FC-GEN-058) ---
# These exit 0 and say why. They are listed under "Not applicable" in the README.

setup: ## Not applicable — there is nothing to set up
	@echo "Nothing to set up: automap.py is standard library only, and this repo"
	@echo "has no pre-commit config to install a hook from."
	@echo "See README > Not applicable."

install: ## Install the package (and its man page) with pip
	pip install .

build: ## Not applicable — nothing is compiled
	@echo "Nothing to build: one pure-Python script."
	@echo "See README > Not applicable."

lint: ## Run the whole gate — every hook, every file
	pre-commit run --all-files

format: ## Not applicable — no formatter is configured here
	@echo "No formatter in this repo: nothing rewrites automap.py."
	@echo "See README > Not applicable."
