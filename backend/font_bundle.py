"""backend/font_bundle.py -- register the fonts shipped inside the app.

Why this exists
---------------
The chart UI offers a list of font families for the chart heading/body, and
the machine running Lytrize usually has almost none of them. Asking Plotly
(the charts render in a QWebEngineView) for a family that is not installed
makes it silently substitute a default, so most of the picker appeared to do
nothing -- the "many fonts are not even working" report. On the box this was
reported from, 5 of 33 offered families existed.

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
Every bundled file is SIL Open Font License 1.1 (or a metric-compatible
substitute under the same terms); each family keeps its own ``LICENSE-*.txt``
next to the font. Proprietary families cannot be shipped, so names like
Georgia / Times New Roman / Arial / Courier New are not included -- see
``SUBSTITUTES`` for the metric-compatible open replacements.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List

log = logging.getLogger(__name__)

# app_assets/fonts/ next to the repo root (backend/ is a sibling of it).
FONT_DIR = Path(__file__).resolve().parent.parent / "app_assets" / "fonts"

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
    return sorted(p for p in FONT_DIR.iterdir()
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
    a font to app_assets/fonts/ is enough to make it selectable.
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
    sub = SUBSTITUTES.get(key)
    if sub and sub.lower() in have:
        return have[sub.lower()]
    return key


def font_face_css() -> str:
    """CSS ``@font-face`` rules so Chromium can use the bundled files.

    The charts render in a QWebEngineView, which has its own font stack and
    cannot see fonts registered with Qt -- so they must be handed to the page
    explicitly. Paths are ``file://`` URLs, which is how the shell itself is
    already loaded.
    """
    if not FONT_DIR.is_dir():
        return ""
    rules: List[str] = []
    for path in font_files():
        try:
            url = path.as_uri()
        except Exception:
            continue
        # One rule per file, all sharing the family name: the browser picks
        # the closest weight it has. The variable fonts cover the full range;
        # the static Lato/Carlito/Caladea files add plain Regular/Bold/Italic.
        rules.append(
            "@font-face{font-family:'%s';src:url('%s') format('truetype');"
            "font-weight:100 900;font-style:normal;font-display:swap;}"
            % (family_of(path), url)
        )
    if not rules:
        return ""
    return "\n".join(rules) + "\n"
