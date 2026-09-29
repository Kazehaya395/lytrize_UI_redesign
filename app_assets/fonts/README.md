# Bundled fonts

The font files Lytrize ships with, so chart typography works on any machine
with no system font packages and no network.

## Why they are bundled

The chart settings panel offers a list of font families. Asking a renderer for
a family that is not installed makes it silently substitute a default, so the
picker appeared broken — most choices produced no visible change. On the
machine this was reported from, only **5 of 33** offered families existed.

Shipping the files removes that whole class of bug: every family the picker
offers is one the app itself provides.

## How they reach the two renderers

The app draws text in two places that do **not** share a font stack, so the
fonts must be registered twice (see `backend/font_bundle.py`):

| Consumer | Mechanism | Registered in |
|---|---|---|
| Qt widgets (preview label, combo boxes, headings) | `QFontDatabase.addApplicationFont` | `desktop/app.py`, after `QApplication` |
| Charts (drawn by Chromium in a `QWebEngineView`) | CSS `@font-face` in the chart shell | `desktop/widgets/plotly_web_view.py` |

Chromium cannot see Qt's font database, so the shell HTML gets explicit
`@font-face` rules with `file://` URLs. Missing either registration leaves half
the UI falling back — tests [31] and [32] guard both.

## Families included

13 families, all SIL Open Font License 1.1 (see the `LICENSE-*.txt` beside
each font):

| Family | Role |
|---|---|
| Inter | app default (body + chart text) |
| Lato | heading / display alternative |
| Roboto, Open Sans, Source Sans 3, Sora | sans-serif options |
| Oswald, Barlow Condensed | condensed display |
| EB Garamond, Caladea | serif options |
| Fira Code, JetBrains Mono | monospace options |
| Carlito | metric-compatible stand-in for Arial / Helvetica / Calibri / Candara / Corbel / Trebuchet MS |
| Caladea | metric-compatible stand-in for Times New Roman / Georgia / Cambria / Constantia |

Proprietary families cannot be redistributed, so **Georgia, Times New Roman,
Arial, Verdana, Courier New, Comic Sans MS, Impact** and friends are not
included. They are still selectable — `font_bundle.SUBSTITUTES` maps each to a
metric-compatible open stand-in, and the original name is kept later in the CSS
font stack so a machine that genuinely has the real thing still uses it.

## Adding a font

1. Drop the `.ttf`/`.otf` into this directory, named `<FamilyName>-<Style>.ttf`
   or `<FamilyName>-Variable.ttf`.
2. Add its `LICENSE-*.txt`.
3. Restart the app.

No code change is needed: `bundled_families()` derives the list from the files
on disk, so a new family is automatically registered with Qt, added to the
picker, and given an `@font-face` rule. If the file name has no spaces but the
display name should (e.g. `OpenSans-Variable.ttf` → "Open Sans"), add it to
`_SPACED_FAMILIES` in `backend/font_bundle.py`.
