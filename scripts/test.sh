#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# test.sh — Run the test suite with coverage
# Usage:  bash scripts/test.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GREEN='\033[0;32m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

if [[ ! -d "venv" ]]; then
    echo "❌ Python venv not found. Run 'bash scripts/setup.sh' first."
    exit 1
fi

source venv/bin/activate

echo -e "${GREEN}🧪 Running tests with coverage...${NC}"
echo ""

python -m pytest tests/ --cov=app --cov-report=term-missing -v "$@"