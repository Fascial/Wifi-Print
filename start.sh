#!/bin/bash
echo "Starting WiFi Print Controller..."

if ! command -v uv &> /dev/null; then
    echo "uv is not installed. Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    source $HOME/.local/bin/env
fi

uv sync
uv run python run.py
