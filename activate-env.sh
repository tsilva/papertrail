#!/usr/bin/env bash
set -euo pipefail

ENV_DIR=".venv"

if [ ! -d "$ENV_DIR" ]; then
    echo "Creating uv virtual environment..."
    uv venv "$ENV_DIR"
    echo "Installing dependencies from pyproject.toml..."
    uv pip install -r pyproject.toml
else
    echo "Using existing virtual environment at $ENV_DIR"
fi

ACTIVATE=""
if [ -f "$ENV_DIR/bin/activate" ]; then
    ACTIVATE="$ENV_DIR/bin/activate"
elif [ -f "$ENV_DIR/Scripts/activate" ]; then
    ACTIVATE="$ENV_DIR/Scripts/activate"
fi

if [ -n "$ACTIVATE" ]; then
    # shellcheck disable=SC1090
    source "$ACTIVATE"
    echo "Environment activated"
else
    echo "Could not find activation script in $ENV_DIR" >&2
    exit 1
fi
