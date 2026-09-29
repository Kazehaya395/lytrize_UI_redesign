# Lytrize UI icon inventory

Every non-ASCII glyph currently hard-coded into UI strings across `desktop/`.
The app crashed on startup (SIGSEGV in `FcCharSetHasChar` via
`QPushButton::sizeHint`) because Qt probed a broken font while shaping these.
An interim fontconfig guard keeps it alive, but the real fix is replacing the
emoji with real icon files.

## How to supply the assets

Create `app_assets/icons/` and drop in SVG files named exactly as below.
`.ico` and `.png` also work; SVG is preferred (scales on HiDPI, recolourable).

- 20x20 viewBox, `stroke="currentColor"`, `fill="none"`, `stroke-width="1.5"`.
- Single-colour line icons match the existing flat UI best.
- Named `<name>.svg`; I'll wire them up and keep a monochrome text fallback so
  a missing file degrades to a label rather than a blank gap.

## The 6 crash glyphs (these are the priority)

| File name | Replaces | Used for | Where |
|---|---|---|---|
| `trash.svg` | U+1F5D1 | Delete / Clear All | `home_page.py:71,205`, `analysis_page.py:126`, `chart_card.py:122`, `data_tools_panel.py:696` |
| `tag.svg` | U+1F3F7 | Categorical / column classification | `column_classifier_panel.py:122,145,180`, `upload_page.py:195` |
| `eye.svg` | U+1F441 | Preview | `data_tools_panel.py:229,309` |
| `text-a.svg` | U+1F170 | Typography | `dashboard_page.py:294,299` |
| `note-pencil.svg` | U+1F5D2 | Notes styling | `dashboard_page.py:231` |
| `tools.svg` | U+1F6E0 | Column Manager | `upload_page.py:200` |

## Navigation & page headers

| File name | Replaces | Used for | Where |
|---|---|---|---|
| `home.svg` | U+1F3E0 | Home | `main_window.py:176`, `home_page.py:55` |
| `folder-open.svg` | U+1F4C2 | Upload | `main_window.py:177`, `home_page.py:99,203`, `upload_page.py:96` |
| `chart-bar.svg` | U+1F4CA | Analysis | `main_window.py:178`, `analysis_page.py:121`, `column_classifier_panel.py:140,167`, `dashboard_page.py:201`, `home_page.py:100` |
| `chart-line.svg` | U+1F4C8 | Dashboard | `main_window.py:179`, `dashboard_page.py:88` |
| `user.svg` | U+1F464 | Profile & Backup | `main_window.py:180`, `profile_page.py:52` |
| `menu.svg` | U+2630 | Sidebar toggle | `main_window.py:116` |

## Actions

| File name | Replaces | Used for | Where |
|---|---|---|---|
| `rocket.svg` | U+1F680 | Start New Analysis | `home_page.py:60` |
| `target.svg` | U+1F3AF | Proceed to Dashboard | `analysis_page.py:130` |
| `save.svg` | U+1F4BE | Save / backup / save CSV | `dashboard_page.py:105`, `home_page.py:98,109`, `profile_page.py:77`, `upload_page.py:140,646` |
| `download.svg` | U+2B07 | Export / export backup | `dashboard_page.py:97,721,840`, `profile_page.py:94`, `chart_card.py:101`, `kpi_card.py:57` |
| `upload.svg` | U+2B06 | Restore backup / move up | `profile_page.py:98`, `chart_card.py:94`, `kpi_card.py:50` |
| `close.svg` | U+2715 | Remove / close | `kpi_card.py:64` |
| `cross.svg` | U+2716 | Clear Preview | `dtype_transformer_panel.py:134` |
| `pencil.svg` | U+270F | Rename / edit chart | `home_page.py:67,204`, `chart_card.py:108` |
| `palette.svg` | U+1F3A8 | Style / colours | `chart_card.py:115`, `chart_style_panel.py:46`, `dashboard_page.py:212` |
| `expand.svg` | U+2B0C | Full width toggle | `chart_card.py:131` |
| `folder.svg` | U+1F4C1 | Import / data location | `profile_page.py:109`, `upload_page.py:101` |
| `image.svg` | U+1F4F7 | Export PNG | `dashboard_page.py:101,757,817,842` |
| `clock.svg` | U+23F3 | Busy / working state | `analysis_page.py:658`, `analysis_config_panel.py:298`, `dashboard_page.py:647,698`, `upload_page.py:295` |
| `arrow-left.svg` | U+2190 | Back | `dashboard_page.py:93` |
| `refresh.svg` | U+1F504 | Refresh / apply | `data_cleaner_panel.py:176`, `dtype_transformer_panel.py:170` |


## Tabs, groups & status

| File name | Replaces | Used for | Where |
|---|---|---|---|
| `search.svg` | U+1F50D | Search / run / detect / dtype tab | `dtype_transformer_panel.py:55,170`, `upload_page.py:125,210`, `data_tools_panel.py:619,683` |
| `preview.svg` | U+1F50E | Preview (secondary) | `data_cleaner_panel.py:257,306,420,474`, `dtype_transformer_panel.py:168` |
| `sheets.svg` | U+1F4D1 | Excel sheets | `excel_loader_panel.py:54`, `upload_page.py:216,344,347` |
| `clipboard.svg` | U+1F4CB | Single sheet radio | `excel_loader_panel.py:77` |
| `link.svg` | U+1F517 | Table join | `excel_loader_panel.py:80,134` |
| `file-text.svg` | U+1F4C4 | Sheet name | `excel_loader_panel.py:173` |
| `broom.svg` | U+1F9F9 | Data cleaner | `data_cleaner_panel.py:162`, `upload_page.py:205`, `data_tools_panel.py:803` |
| `soap.svg` | U+1F9FC | Data cleaner (alt tab) | `data_tools_panel.py:802` |
| `puzzle.svg` | U+1F9E9 | Column manager | `data_tools_panel.py:800` |
| `siren.svg` | U+1F6A8 | Outliers | `data_tools_panel.py:804` |
| `hash.svg` | U+1F522 | Numeric / KPI text / value labels | `dashboard_page.py:279`, `data_cleaner_panel.py:188`, `excel_loader_panel.py:227`, `upload_page.py:525`, `chart_style_panel.py:83`, `data_tools_panel.py:801` |
| `text.svg` | U+1F524 | Text clean / typography | `dashboard_page.py:222`, `data_cleaner_panel.py:172`, `excel_loader_panel.py:227`, `upload_page.py:525`, `chart_style_panel.py:99` |
| `calendar.svg` | U+1F4C5 | Date/time | `column_classifier_panel.py:150,193`, `excel_loader_panel.py:227`, `upload_page.py:526` |
| `scissors.svg` | U+2702 | String ops | `data_cleaner_panel.py:184` |
| `check.svg` | U+2705 | Pass / confirm / apply | `column_classifier_panel.py:212`, `data_cleaner_panel.py:180,258,307,353,372`, `data_tools_panel.py:520` |
| `cross-circle.svg` | U+274C | Fail / error | `column_manager_panel.py:614,657`, `data_cleaner_panel.py:354,710`, `dtype_transformer_panel.py:309`, `data_tools_panel.py:520` |
| `warning.svg` | U+26A0 | Warning | `column_classifier_panel.py:352`, `dtype_transformer_panel.py:306,319` |
| `dot.svg` | U+26AB | Markers group | `chart_style_panel.py:123` |
| `square.svg` | U+2B1C | Null count | `data_cleaner_panel.py:355,711`, `data_tools_panel.py:520` |
| `ruler.svg` | U+1F4D0 | Layout | `dashboard_page.py:240`, `chart_style_panel.py:58` |
| `note.svg` | U+1F4DD | Dashboard info | `dashboard_page.py:169` |
| `microscope.svg` | U+1F52C | Select analysis type | `analysis_page.py:152` |

## NOT icons -- leave these as text

These are mathematical or arrow characters used inside prose and formulas, not
UI affordances. Converting them to icons would be wrong, and they render fine
from DejaVu (they were not in the crash set):

- U+2192 rightwards arrow
- U+2212 minus sign
- U+2215 division slash
- U+00D7 multiplication sign
- U+00F7 division sign
- U+00B7 middle dot
- U+FE0F variation selector (a modifier applied to the emoji above; it
  disappears along with them)

## Total

**49 icon files.** 6 are the crash-critical ones; the rest prevent a
regression the moment someone adds a new emoji.

Once the assets are in place I will:

1. Add an `icon(name)` helper in `desktop/theme.py` that loads from
   `app_assets/icons/` with caching and a graceful text fallback.
2. Replace the glyphs, keeping the label text intact.
3. Remove the fontconfig guard once no icon glyph remains, and add a test
   asserting `desktop/` contains no emoji outside prose.
