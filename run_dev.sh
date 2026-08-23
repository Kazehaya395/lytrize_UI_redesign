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
    if ! python3 -c "import PySide6" 2>/dev/null; then
        echo "ERROR: PySide6 not found. Please install dependencies first:"
        echo ""
        echo "  python3 -m pip install --user \\"
        echo "      pandas>=2.2.0,<3.0.0 \\"
        echo "      plotly>=5.22.0,<6.0.0 \\"
        echo "      openpyxl>=3.1.0,<4.0.0 \\"
        echo "      scipy>=1.12.0,<2.0.0 \\"
        echo "      statsmodels>=0.14.0,<0.15.0 \\"
        echo "      pycountry>=23.12.11,<25.0.0 \\"
        echo "      PySide6>=6.11.0,<7.0.0 \\"
        echo "      PySide6-Addons>=6.11.0,<7.0.0"
        echo ""
        echo "Or install from requirements.txt:"
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
