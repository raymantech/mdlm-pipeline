# ===== MDLM Makefile =====
# 项目路径：/Users/ray/PycharmProjects/pythonProject-ray/MDLM1.0

SHELL := /bin/bash

PYTHON := python3
VENV := .venv
PIP := $(VENV)/bin/pip
PY := $(VENV)/bin/python

RELEASE_SCRIPT := backend/build_site_release_with_qishui.py

RELEASE_ARGS :=
RELEASE_NO_QISHUI_ARGS := --no-qishui

NPM := npm

.PHONY: setup venv install release release-no-qishui clean

setup: venv install

venv:
	@if [ ! -d "$(VENV)" ]; then \
		echo "Creating virtual environment..."; \
		$(PYTHON) -m venv $(VENV); \
	fi

install:
	@if [ -f requirements.txt ]; then \
		echo "Installing dependencies..."; \
		$(PIP) install -r requirements.txt; \
	else \
		echo "requirements.txt not found, skipping pip install."; \
	fi

release: setup
	@echo "Running full release (with qishui if available)"
	@$(PY) $(RELEASE_SCRIPT) $(RELEASE_ARGS)
	@echo "Release done."

release-no-qishui: setup
	@echo "Running release without qishui"
	@$(PY) $(RELEASE_SCRIPT) $(RELEASE_NO_QISHUI_ARGS)
	@echo "Release-no-qishui done."

clean:
	rm -rf $(VENV)
	rm -rf __pycache__ */__pycache__