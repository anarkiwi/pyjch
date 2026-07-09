#!/bin/sh
set -e
black --check src/pyjch tests scripts
pylint src/pyjch tests scripts
pytest -n auto --cov=pyjch --cov-report=term-missing --cov-fail-under=85
