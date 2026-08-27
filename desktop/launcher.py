"""desktop/launcher.py — CLI entry point called by /usr/local/bin/lytrize"""


import subprocess
import sys
from pathlib import Path


INSTALL_BASE = Path("/opt/lytrize")
VENV_PY      = INSTALL_BASE / "venv" / "bin" / "python"
APP          = INSTALL_BASE / "desktop" / "app.py"


def _resolve_python() -> str:
    """Return the path to the Python interpreter to use."""
    if VENV_PY.exists():
        return str(VENV_PY)
    return "python3"


if __name__ == "__main__":
    python = _resolve_python()
    result = subprocess.run([python, str(APP)] + sys.argv[1:])
    sys.exit(result.returncode)