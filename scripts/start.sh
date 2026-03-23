#!/usr/bin/env bash
# Start the DeepVoice FastAPI server.
#
# Usage:
#   ./scripts/start.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

# Activate virtual environment if it exists
if [ -d ".venv" ]; then
    echo "Activating virtual environment (.venv)..."
    # shellcheck disable=SC1091
    source .venv/bin/activate
elif [ -d "venv" ]; then
    echo "Activating virtual environment (venv)..."
    # shellcheck disable=SC1091
    source venv/bin/activate
else
    echo "Warning: No virtual environment found. Using system Python."
fi

# Check for .env file
if [ ! -f ".env" ]; then
    echo "Error: .env file not found."
    echo "Copy .env.example to .env and fill in the values:"
    echo "  cp .env.example .env"
    exit 1
fi

echo "Starting DeepVoice server..."
exec uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload
