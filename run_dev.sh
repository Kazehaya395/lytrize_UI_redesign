#!/bin/bash
# run_dev.sh — Quick launcher for testing the Qt app during development
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "======================================"
echo "  Lytrize Qt Dev Launcher"
echo "======================================"
echo ""

# Check if Python is available
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 not found. Please install Python 3.11+"
    exit 1
fi

# Use venv if available
if [ -d "venv" ] && [ -x "venv/bin/python" ]; then
    echo "  Using venv: venv/bin/python"
    echo ""
    echo "Starting Lytrize..."
    echo "  Entry point: desktop/app.py"
    echo "  Press Ctrl+C to stop"
    echo ""
    exec venv/bin/python desktop/app.py
else
    # Check if required packages are installed
    echo "Checking dependencies..."
    if ! python3 -c "import PySide6, numpy, pandas, polars, pyarrow, scipy, openpyxl" 2>/dev/null; then
        echo "ERROR: One or more Lytrize dependencies are missing. Install them with:"
        echo "  python3 -m pip install --user -r requirements.txt"
        exit 1
    fi

    echo ""
    echo "Starting Lytrize..."
    echo "  Entry point: desktop/app.py"
    echo "  Press Ctrl+C to stop"
    echo ""

    exec python3 desktop/app.py
fi
