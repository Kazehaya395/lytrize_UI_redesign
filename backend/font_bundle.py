"""backend/font_bundle.py -- register the fonts shipped inside the app.

Why this exists
---------------
The chart UI offers font families for chart headings and body text. The
machine running Lytrize may not have those families installed. Chromium in
QWebEngineView does not use Qt's registered font database, so it can silently
substitute a default unless the font files are also provided through CSS.

Shipping the font files with the app removes the whole class of problem: the
picker can only offer a family the app itself provides, so every option
really changes the chart, on any machine, with no system font packages and no
network.

Two consumers, and both need registering:

* **Qt** (``QFontDatabase.addApplicationFont``) for the widgets -- the settings
  panel's live "Preview - 123 Inter" labels, the font combo boxes, and the
  dashboard style panel.
* **The web shell** (CSS ``@font-face``) for the charts themselves, which are
  drawn by Chromium and do NOT see Qt's font database at all.

Licensing
---------
Open font packs keep their ``LICENSE-*.txt`` files beside the fonts.
``SUBSTITUTES`` maps familiar font picker names to bundled alternatives when
the requested family itself is unavailable.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Dict, List

log = logging.getLogger(__name__)

# Keep fonts beside the other shipped assets so source and package layouts
# resolve to the same location. ``modules.runtime_paths`` is the single
# source of truth for the frozen (PyInstaller) vs source-tree layout.
try:
    from modules.runtime_paths import assets_root as _assets_root
    FONT_DIR = _assets_root() / "fonts"
except Exception:  # pragma: no cover - never brick the shell over fonts
    FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"

# Open, metric-compatible stand-ins for the proprietary families the picker
# used to offer. Keyed by the name the user sees -> the family we ship.
SUBSTITUTES: Dict[str, str] = {
    "Arial": "Carlito",
    "Helvetica": "Carlito",
    "Calibri": "Carlito",
    "Candara": "Carlito",
    "Corbel": "Carlito",
    "Trebuchet MS": "Carlito",
    "Verdana": "Roboto",
    "Times New Roman": "Caladea",
    "Georgia": "Caladea",
    "Cambria": "Caladea",
    "Constantia": "Caladea",
    "Palatino Linotype": "EB Garamond",
    "Courier New": "JetBrains Mono",
    "Consolas": "JetBrains Mono",
    "Lucida Console": "JetBrains Mono",
    "Comic Sans MS": "Lato",
    "Impact": "Oswald",
}

# Families shown in the picker, in display order. Every one is either bundled
# here or already installed; chart_display.available_fonts() decides, so this
# stays a preference list rather than a promise.
PREFERRED_ORDER: List[str] = [
    "Inter", "Lato", "Roboto", "Open Sans", "Source Sans 3", "Sora",
    "Oswald", "Barlow Condensed", "EB Garamond", "Caladea",
    "Fira Code", "JetBrains Mono", "Carlito",
]

_qt_loaded = False
_registered: Dict[str, int] = {}

# File-name stem -> displayed family, for the Google Fonts files that are
# shipped without spaces in the name.
_SPACED_FAMILIES = {
    "OpenSans": "Open Sans",
    "SourceSans3": "Source Sans 3",
    "BarlowCondensed": "Barlow Condensed",
    "EBGaramond": "EB Garamond",
    "FiraCode": "Fira Code",
    "JetBrainsMono": "JetBrains Mono",
    "NotoSans": "Noto Sans",
    "NotoSerif": "Noto Serif",
}


def font_files() -> List[Path]:
    """Every bundled font file, in a stable order."""
    if not FONT_DIR.is_dir():
        return []
    # Keep supporting the original flat font bundle and discover newer
    # family packs stored in named subdirectories under assets/fonts.
    return sorted(p for p in FONT_DIR.rglob("*")
                  if p.suffix.lower() in (".ttf", ".otf") and p.is_file())


def family_of(path: Path) -> str:
    """Family name for a file, derived from its name.

    Naming convention: ``<FamilyName>-<Style>.ttf`` or
    ``<FamilyName>-Variable.ttf``, where the family is written as it should be
    displayed. Google Fonts ship the files as ``OpenSans-Variable.ttf``, so the
    known no-space families are spelled out via _SPACED_FAMILIES rather than
    forcing the files to be renamed.
    """
    stem = path.stem
    for suffix in ("-Variable", "-Regular", "-Bold", "-Italic",
                   "-BoldItalic", "-Light", "-Medium", "-SemiBold"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return _SPACED_FAMILIES.get(stem, stem)


def register_qt_fonts() -> Dict[str, int]:
    """Register every bundled font with Qt. Idempotent and safe to call twice.

    Returns a ``{family: font_id}`` map. Requires a QApplication to exist
    (QFontDatabase needs one), so it must be called AFTER the app object is
    constructed -- unlike the fontconfig guard, which has to run before it.
    """
    global _qt_loaded
    if _qt_loaded:
        return dict(_registered)

    try:
        from PySide6.QtGui import QFontDatabase
    except Exception:
        log.debug("QFontDatabase unavailable; bundled fonts not registered")
        return {}

    for path in font_files():
        try:
            fid = QFontDatabase.addApplicationFont(str(path))
        except Exception:
            log.debug("could not register %s", path, exc_info=True)
            continue
        if fid < 0:
            log.warning("Qt rejected bundled font %s", path.name)
            continue
        try:
            families = QFontDatabase.applicationFontFamilies(fid)
        except Exception:
            families = []
        # A variable font reports several families ("Fira Code",
        # "Fira Code Light"); register them all so either name resolves.
        for fam in families or [family_of(path)]:
            _registered.setdefault(fam, fid)

    _qt_loaded = True
    log.info("Registered %d bundled font families from %s",
             len(_registered), FONT_DIR)
    return dict(_registered)


def bundled_families() -> List[str]:
    """Family names the app ships, ordered by preference then alphabetically.

    Derived from the files themselves rather than a hard-coded list, so adding
    a font to backend/assets/fonts/ is enough to make it selectable.
    """
    fams = {family_of(p) for p in font_files()}
    ordered = [f for f in PREFERRED_ORDER if f in fams]
    ordered += sorted(f for f in fams if f not in ordered)
    return ordered


def resolve(name: str) -> str:
    """Map a requested family to one the app can actually render.

    A bundled family is returned as-is. A proprietary name the picker still
    shows is mapped to its metric-compatible stand-in, so the familiar label
    keeps working. Anything unknown falls through unchanged -- Qt/Chromium
    substitutes, which is the old behaviour, but now only for names we do not
    recognise at all.
    """
    key = str(name or "").strip()
    if not key:
        return key
    have = {f.lower(): f for f in bundled_families()}
    if key.lower() in have:
        return have[key.lower()]
    substitutes = {name.lower(): value for name, value in SUBSTITUTES.items()}
    sub = substitutes.get(key.lower())
    if sub and sub.lower() in have:
        return have[sub.lower()]
    return key


def font_face_css(*, embed: bool = False, families=None) -> str:
    """CSS ``@font-face`` rules so Chromium can use the bundled files.

    The charts render in a QWebEngineView, which has its own font stack and
    cannot see fonts registered with Qt -- so they must be handed to the page
    explicitly. Use local paths for the Qt chart shell and embedded data URLs
    for self-contained dashboard HTML.
    """
    if not FONT_DIR.is_dir():
        return ""
    wanted = None
    if families is not None:
        requested = {str(name).strip() for name in families if str(name).strip()}
        wanted = {name.lower() for name in requested}
        wanted.update(resolve(name).lower() for name in requested)
    rules: List[str] = []
    for path in font_files():
        family = family_of(path)
        if wanted is not None and family.lower() not in wanted:
            continue
        try:
            if embed:
                encoded = base64.b64encode(path.read_bytes()).decode("ascii")
                url = f"data:font/ttf;base64,{encoded}"
            else:
                url = path.as_uri()
        except Exception:
            continue
        # One rule per file, all sharing the family name: the browser picks
        # the closest weight it has. The variable fonts cover the full range;
        # the static Lato/Carlito/Caladea files add plain Regular/Bold/Italic.
        stem = path.stem.lower()
        variable = "variable" in stem
        weight = ("100 900" if variable else
                  "700" if "bold" in stem else
                  "600" if "semibold" in stem else
                  "500" if "medium" in stem else
                  "300" if "light" in stem else "400")
        style = "italic" if "italic" in stem else "normal"
        rules.append("@font-face{font-family:'%s';src:url('%s') format('truetype');"
                     "font-weight:%s;font-style:%s;font-display:swap;}"
                     % (family, url, weight, style))
    if not rules:
        return ""
    return "\n".join(rules) + "\n"
