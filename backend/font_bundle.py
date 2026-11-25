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

# Open, metric-compatible stand-ins for proprietary names the app does NOT
# ship. The app bundles the Microsoft core fonts themselves, so Arial,
# Calibri, Georgia, Times New Roman, Verdana, Trebuchet MS, Consolas,
# Courier New, Comic Sans MS, Impact, Cambria, Candara, Corbel and Constantia
# resolve to their real bundled faces (see bundled_families()). Only the
# names below have no shipped counterpart and still need a stand-in.
SUBSTITUTES: Dict[str, str] = {
    "Helvetica": "Carlito",
    "Palatino Linotype": "EB Garamond",
    "Lucida Console": "JetBrains Mono",
}

# Families shown in the picker, in display order. Every one is either bundled
# here or already installed; chart_display.available_fonts() decides, so this
# stays a preference list rather than a promise.
PREFERRED_ORDER: List[str] = [
    "Inter", "Lato", "Roboto", "Open Sans", "Source Sans 3", "Sora",
    "Oswald", "Barlow Condensed", "EB Garamond", "Caladea", "Carlito",
    "Arial", "Calibri", "Georgia", "Times New Roman", "Verdana",
    "Trebuchet MS", "Tahoma", "Cambria", "Candara", "Constantia", "Corbel",
    "Courier New", "Consolas", "Andale Mono", "Comic Sans MS", "Impact",
    "Arial Black",
    "Fira Code", "JetBrains Mono",
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
    "NotoEmoji": "Noto Emoji",
}

# The bundled Microsoft core fonts ship under their classic 8.3 file names
# (``timesbd.ttf`` etc.), which carry no family/style information a stem
# parser can see -- so every file gets an explicit entry: stem (lower cased)
# -> (display family, weight, style). The display names match the family
# names Qt reads from each file's name table, which is what the Typography
# picker writes into the option and what Chromium must see in @font-face.
_MS_FONT_FILES = {
    "andalemo": ("Andale Mono", 400, "normal"),
    "arial": ("Arial", 400, "normal"),
    "arialbd": ("Arial", 700, "normal"),
    "ariali": ("Arial", 400, "italic"),
    "arialbi": ("Arial", 700, "italic"),
    "ariblk": ("Arial Black", 900, "normal"),
    "calibri": ("Calibri", 400, "normal"),
    "calibrib": ("Calibri", 700, "normal"),
    "calibrii": ("Calibri", 400, "italic"),
    "calibriz": ("Calibri", 700, "italic"),
    "cambriab": ("Cambria", 700, "normal"),
    "cambriai": ("Cambria", 400, "italic"),
    "cambriaz": ("Cambria", 700, "italic"),
    "candara": ("Candara", 400, "normal"),
    "candarab": ("Candara", 700, "normal"),
    "candarai": ("Candara", 400, "italic"),
    "candaraz": ("Candara", 700, "italic"),
    "comic": ("Comic Sans MS", 400, "normal"),
    "comicbd": ("Comic Sans MS", 700, "normal"),
    "consola": ("Consolas", 400, "normal"),
    "consolab": ("Consolas", 700, "normal"),
    "consolai": ("Consolas", 400, "italic"),
    "consolaz": ("Consolas", 700, "italic"),
    "constan": ("Constantia", 400, "normal"),
    "constanb": ("Constantia", 700, "normal"),
    "constani": ("Constantia", 400, "italic"),
    "constanz": ("Constantia", 700, "italic"),
    "corbel": ("Corbel", 400, "normal"),
    "corbelb": ("Corbel", 700, "normal"),
    "corbeli": ("Corbel", 400, "italic"),
    "corbelz": ("Corbel", 700, "italic"),
    "cour": ("Courier New", 400, "normal"),
    "courbd": ("Courier New", 700, "normal"),
    "courbi": ("Courier New", 700, "italic"),
    "couri": ("Courier New", 400, "italic"),
    "georgia": ("Georgia", 400, "normal"),
    "georgiab": ("Georgia", 700, "normal"),
    "georgiai": ("Georgia", 400, "italic"),
    "georgiaz": ("Georgia", 700, "italic"),
    "impact": ("Impact", 400, "normal"),
    "tahoma": ("Tahoma", 400, "normal"),
    "times": ("Times New Roman", 400, "normal"),
    "timesbd": ("Times New Roman", 700, "normal"),
    "timesbi": ("Times New Roman", 700, "italic"),
    "timesi": ("Times New Roman", 400, "italic"),
    "trebuc": ("Trebuchet MS", 400, "normal"),
    "trebucbd": ("Trebuchet MS", 700, "normal"),
    "trebucbi": ("Trebuchet MS", 700, "italic"),
    "trebucit": ("Trebuchet MS", 400, "italic"),
    "verdana": ("Verdana", 400, "normal"),
    "verdanab": ("Verdana", 700, "normal"),
    "verdanai": ("Verdana", 400, "italic"),
    "verdanaz": ("Verdana", 700, "italic"),
    # A symbol/dingbat pack, not a text face -- shipped for completeness but
    # never offered in the text picker.
    "webdings": ("Webdings", 400, "normal"),
}

# Families that must not appear in the text picker (still registered with Qt
# and Chromium so an explicitly stored option can resolve). "Noto Emoji" is a
# monochrome fallback for UI glyphs (toolbar icons, status markers) on machines
# without an emoji font -- it is not a text face.
_PICKER_EXCLUDE = {"Webdings", "Noto Emoji"}


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
    ms = _MS_FONT_FILES.get(stem.lower())
    if ms is not None:
        return ms[0]
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
    fams = {family_of(p) for p in font_files()} - _PICKER_EXCLUDE
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


_CSS_CACHE: dict = {}
_CSS_CACHE_MAX = 8


def font_face_css(*, embed: bool = False, families=None) -> str:
    """CSS ``@font-face`` rules so Chromium can use the bundled files.

    The charts render in a QWebEngineView, which has its own font stack and
    cannot see fonts registered with Qt -- so they must be handed to the page
    explicitly. Use local paths for the Qt chart shell and embedded data URLs
    for self-contained dashboard HTML.

    Results are memoized: the dashboard preview rebuilds the report on every
    gesture, and re-base64-encoding every bundled font on each rebuild was a
    large, entirely reusable cost.
    """
    cache_key = (bool(embed),
                 tuple(sorted({str(f).strip() for f in (families or [])
                               if str(f).strip()})))
    hit = _CSS_CACHE.get(cache_key)
    if hit is not None:
        return hit
    css = _font_face_css_uncached(embed=embed, families=families)
    if len(_CSS_CACHE) >= _CSS_CACHE_MAX:
        _CSS_CACHE.clear()
    _CSS_CACHE[cache_key] = css
    return css


def _font_face_css_uncached(*, embed: bool = False, families=None) -> str:
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
        ms = _MS_FONT_FILES.get(stem)
        if ms is not None:
            weight = str(ms[1])
            style = ms[2]
        else:
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
