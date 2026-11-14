"""Runtime location of the bundled asset tree.

Every asset lookup in the backend used to compute its path from
``__file__`` (``Path(__file__).resolve().parents[N] / "assets"``), assuming
the source-tree layout. Inside a PyInstaller bundle that assumption breaks:
modules are resolved from the embedded PYZ archive, so ``__file__`` no
longer matches the on-disk tree the assets were extracted to. A top-level
``font_bundle`` import resolves its assets relative to the bundle root
itself, which is why the frozen app registered 0 bundled fonts and inlined
map GeoJSON lookups would fail the same way.

One helper, one answer:

* frozen (PyInstaller): ``<bundle>/backend/assets`` — where the build
  unpacks ``backend\\assets`` (see ``build_exe.ps1``);
* source tree: ``<repo>/backend/assets``.
"""
from __future__ import annotations

import sys
from pathlib import Path


def assets_root() -> Path:
    """Return the directory holding the bundled runtime assets."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "backend" / "assets"
    # This file is <repo>/backend/modules/runtime_paths.py.
    return Path(__file__).resolve().parents[1] / "assets"
