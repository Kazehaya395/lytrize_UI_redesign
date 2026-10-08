"""Shared flat-section helpers + 3-tab builder contract (no cards)."""
from __future__ import annotations
import logging
from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QApplication, QColorDialog, QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QLineEdit, QPushButton, QSizePolicy, QSlider, QSpinBox, QStyle, QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem, QVBoxLayout, QWidget)
from desktop.theme import group_label_style, label_style, resolve_color, input_style, PAGE_MARGINS

log = logging.getLogger(__name__)

def _fallback(mode):
    return None
CTA_TEXT = '\u25b6 Generate Charts'
CTA_TEXT_EDIT = '\U0001f4be Update Chart'
COMPACT_W = 240
PAGE_SPACING = 10
ROW_SPACING = 6
HINT_GAP = 4
SECTION_GAP = 14
# Lines reserved for a section's description row. Sections in the same row
# share this height so their controls line up whatever the text says (and a
# section with no description still reserves the slot instead of jumping up).
HINT_ROW_LINES = 2
PILL_W = 128
# Compact Generate field width -- the annotated design's 210px field. Shared
# by every builder (base owns it so ``FLOW_MIN_COL`` cannot drift from it).
GEN_W = 210
# Minimum width one Generate section box needs before the next section wraps
# onto the following line: the compact field (``GEN_W``) plus the section's
# own 12px side margins. See ``FlowGrid``.
FLOW_MIN_COL = GEN_W + 24
# Generous default pane width for a page that has not been shown yet, so a
# headless build still gets a sane line-break instead of one item per line.
FLOW_ASSUMED_W = 1440
_CHECKED_ROLE = Qt.UserRole + 1
_CHECK_BOX = True
CHECK_ROW_H = 28
MAX_VISIBLE_ROWS = 8
POPUP_MIN_W = 260
def section_label(text):
    lbl = QLabel(text)
    lbl.setStyleSheet(group_label_style())
    lbl.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    lbl.setProperty('theme_role', 'section')
    return lbl
def hint_label(text):
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setStyleSheet(label_style())
    lbl.setProperty('theme_role', 'label')
    return lbl
def cap_widget(w, width=COMPACT_W):
    w.setFixedWidth(width)
    w.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    w.setProperty('capped', True)
    return w
def _row_pen(option):
    try:
        return option.widget.palette().color(QPalette.Text)
    except Exception:
        try:
            return QApplication.palette().color(QPalette.Text)
        except Exception:
            return QColor('#e2e8f0')
class _CheckDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        painter.save()
        try:
            checked = bool(index.data(_CHECKED_ROLE))
            hover = bool(option.state & QStyle.State_MouseOver)
            sel = bool(option.state & QStyle.State_Selected)
            pal = option.palette if hasattr(option, 'palette') else QApplication.palette()
            bg = pal.color(QPalette.Highlight) if (hover or sel) else pal.color(QPalette.Base)
            painter.fillRect(option.rect, bg)
            r = option.rect.adjusted(8, 0, -8, 0)
            box = QStyleOptionButton()
            box.rect = QRect(r.left(), r.center().y() - 8, 16, 16)
            box.state = QStyle.State_Enabled | (QStyle.State_On if checked else QStyle.State_Off)
            QApplication.style().drawControl(QStyle.CE_CheckBox, box, painter)
            painter.setPen(_row_pen(option))
            text = index.data(Qt.DisplayRole) or ''
            painter.drawText(r.adjusted(26, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, str(text))
        finally:
            painter.restore()
    def sizeHint(self, option, index):
        return QSize(200, CHECK_ROW_H)
class _MultiSelectPopup(QWidget):
    selection_changed = Signal(list)
    def __init__(self, items=None, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName('multi_select_popup')
        self._items = list(items or [])
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(2)
        self.list = QListWidget()
        self.list.setObjectName('multi_select_list')
        try:
            self.list.setItemDelegate(_CheckDelegate())
        except Exception:
            pass
        for it in self._items:
            li = QListWidgetItem(str(it))
            li.setData(_CHECKED_ROLE, False)
            self.list.addItem(li)
        self.list.itemPressed.connect(self._on_row_pressed)
        lay.addWidget(self.list)
        foot = QHBoxLayout()
        self.lbl_count = QLabel('')
        self.lbl_count.setProperty('theme_role', 'label')
        try:
            self.lbl_count.setStyleSheet(label_style())
        except Exception:
            pass
        foot.addWidget(self.lbl_count)
        foot.addStretch()
        bclear = QPushButton('Clear')
        bclear.setProperty('kind', 'ghost')
        bclear.clicked.connect(self._clear)
        foot.addWidget(bclear)
        bdone = QPushButton('Done')
        bdone.setProperty('kind', 'ghost')
        bdone.clicked.connect(self.hide)
        foot.addWidget(bdone)
        lay.addLayout(foot)
        self._sync_footer()
    def current_selected(self):
        out = []
        for i in range(self.list.count()):
            li = self.list.item(i)
            if li.data(_CHECKED_ROLE):
                out.append(li.text())
        return out
    def set_checked(self, values):
        want = set(values or [])
        for i in range(self.list.count()):
            li = self.list.item(i)
            li.setData(_CHECKED_ROLE, li.text() in want)
        self._sync_footer()
    def _on_row_pressed(self, item):
        try:
            item.setData(_CHECKED_ROLE, not bool(item.data(_CHECKED_ROLE)))
        except Exception:
            pass
        self._sync_footer()
        self.selection_changed.emit(self.current_selected())
    def _clear(self):
        self.set_checked([])
        self.selection_changed.emit([])
    def _sync_footer(self):
        try:
            self.lbl_count.setText(f'{len(self.current_selected())} selected')
        except Exception:
            pass
    def show_below(self, anchor):
        try:
            pt = anchor.mapToGlobal(QPoint(0, anchor.height()))
            self.setMinimumWidth(max(POPUP_MIN_W, anchor.width()))
            self.move(pt)
        except Exception:
            pass
        self.show()
class MultiSelectCombo(QComboBox):
    selection_changed = Signal(list)
    # Shown when nothing is selected. Intentionally EMPTY: a sentence like
    # "Dropdown with checkboxes; click to expand" is instruction text, not a
    # value, and reading it as the current selection was confusing -- it
    # looked like a real choice sitting in the field.
    PLACEHOLDER = ''
    def __init__(self, items=None, parent=None):
        super().__init__(parent)
        self._items = list(items or [])
        self._popup = None
        self.setEditable(False)
        try:
            self.setInsertPolicy(QComboBox.NoInsert)
        except Exception:
            pass
        self._update_text()
    def showPopup(self):
        try:
            if self._popup is None:
                self._popup = _MultiSelectPopup(self._items, self)
                self._popup.selection_changed.connect(self._on_popup_changed)
            else:
                cur = set(self.current_selected())
                self._popup.set_checked(sorted(cur))
            self._popup.show_below(self)
        except Exception:
            super().showPopup()
    def hidePopup(self):
        try:
            super().hidePopup()
        except Exception:
            pass
    def keyPressEvent(self, e):
        super().keyPressEvent(e)
    def current_selected(self):
        if self._popup is not None:
            try:
                return self._popup.current_selected()
            except Exception:
                pass
        return list(getattr(self, '_sel', []))
    def set_selection(self, values):
        self._sel = list(values or [])
        if self._popup is not None:
            try:
                self._popup.set_checked(self._sel)
            except Exception:
                pass
        self._update_text()
    def set_item_checked(self, name, checked):
        cur = self.current_selected()
        if checked and name not in cur:
            cur.append(name)
        elif (not checked) and name in cur:
            cur = [c for c in cur if c != name]
        self.set_selection(cur)
    def clear_all(self):
        self.set_selection([])
    def _after_change(self):
        self._update_text()
        try:
            self.selection_changed.emit(self.current_selected())
        except Exception:
            pass
    def _on_popup_changed(self, sel):
        self._sel = list(sel)
        self._update_text()
        try:
            self.selection_changed.emit(list(sel))
        except Exception:
            pass
    def _update_text(self):
        sel = list(getattr(self, '_sel', []))
        try:
            self.clear()
            if not sel:
                self.addItem(self.PLACEHOLDER)
            elif len(sel) <= 3:
                self.addItem(', '.join(sel))
            else:
                self.addItem(f'{len(sel)} selected')
        except Exception:
            pass
def multi_select_combo(items):
    return MultiSelectCombo(list(items or []))
def page_root():
    root = QWidget()
    rl = QVBoxLayout(root)
    rl.setContentsMargins(*PAGE_MARGINS)
    rl.setSpacing(PAGE_SPACING)
    return root, rl
def section_box(title, body, hint=''):
    """A titled section: header, description, then the control.

    The description row is a FIXED-height slot whether or not there is
    anything to put in it. Without that reservation each section sized its
    header block to its own text, so in a row of siblings a two-line hint
    pushed its control further down than a one-line (or absent) hint and the
    controls ended up on a staircase instead of one line. The rule is that
    the control sits at the same height in every section of the row, no
    matter what the wording or line count is.
    """
    wrap = QWidget()
    wl = QVBoxLayout(wrap)
    wl.setContentsMargins(12, 4, 12, 4)
    wl.setSpacing(HINT_GAP)
    wl.addWidget(section_label(title))
    hint_w = hint_label(hint)
    try:
        # Two lines' worth, measured from the actual hint font so it stays
        # correct across theme/scale changes rather than being a magic number.
        hint_w.setFixedHeight(hint_w.fontMetrics().height() * HINT_ROW_LINES)
    except Exception:
        pass
    wl.addWidget(hint_w)
    wl.addWidget(body)
    return wrap
class FlowGrid(QWidget):
    """Generate sections packed across the pane, wrapping onto new lines.

    The old panels were Streamlit ones and a ``st.columns(n)`` column always
    spanned its share of the page, so the controls sat side by side across the
    viewport. A fixed "three sections per row" grid instead stopped a third of
    the way across and left the whole right-hand side of the tab empty -- the
    reported "why is this leaving empty space, fill the viewport" -- while
    also making the page taller than the pane it sits in.

    This is the fix. A line holds as many sections as fit at ``min_col``
    width; the sections on that line stretch to equal shares of the pane, so
    the line is filled edge to edge and every line has the SAME column count,
    which is what keeps the wrapped remainder aligned under the columns above
    it. What does not fit wraps onto the next line and stays at the left.
    The pane then only ever needs its vertical scrollbar for the wrapped
    lines, never for a grid that was wider than the space it was given.

    Re-flows on every resize: a narrow pane drops to fewer columns per line
    instead of clipping, a wide one to more (the eight categorical controls
    fit on one line once the pane is wide enough for them).
    """

    def __init__(self, min_col=FLOW_MIN_COL, gap=SECTION_GAP, parent=None):
        super().__init__(parent)
        self._min_col = int(min_col)
        self._gap = int(gap)
        self._items = []
        self._hidden = set()
        self._lines = []
        self._cols = 0
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(PAGE_SPACING)
        self._outer = outer
        # One column's worth is the floor, NOT the current layout's minimum.
        # Qt refuses to shrink a widget below the minimum its CURRENT layout
        # needs, so a line of six sections would pin the page at ~1470px and
        # the pane could never get small enough to trigger the re-flow that
        # would have broken those six into fewer columns -- the exact
        # chicken-and-egg that leaves a page wider than its viewport. An
        # explicit minimum lets the resize land; the line break is then
        # recomputed from the new width.
        try:
            self.setMinimumWidth(self._min_col)
        except Exception:
            pass

    # -- Content -----------------------------------------------------------
    def add_section(self, widget):
        """Add one section box, in slot order, and re-flow the lines."""
        try:
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        except Exception:
            pass
        self._items.append(widget)
        self._relayout(force=True)
        return widget

    def set_section_hidden(self, widget, hidden):
        """Show or hide one section box and re-flow the survivors.

        A hidden box must leave the LINE MATHS as well as the screen: the
        re-flow divides the visible boxes into equal-width slots per line, so a
        box that merely had ``setVisible(False)`` would still be counted and
        every surviving section on its line would keep a hole where it was.
        This is what lets the map form show only the fields the selected mode
        actually reads without the remaining rows packing unevenly.
        """
        was = widget in self._hidden
        now = bool(hidden)
        if was == now:
            return
        if now:
            self._hidden.add(widget)
        else:
            self._hidden.discard(widget)
        widget.setVisible(not now)
        self._relayout(force=True)

    def visible_sections(self):
        """The boxes currently shown, in slot order (what the maths uses)."""
        return tuple(w for w in self._items if w not in self._hidden)

    def columns(self):
        """Columns per line at the last re-flow (0 before the first one)."""
        return self._cols

    def sections(self):
        """The section boxes, in slot order (what ``add_section`` took)."""
        return tuple(self._items)

    def line_count(self):
        return len(self._lines)

    # -- Layout ------------------------------------------------------------
    def _col_count(self):
        # Only VISIBLE sections count: a hidden box takes no slot on screen, so
        # counting it would leave a hole at the end of every line it touched.
        n = len(self.visible_sections())
        if n <= 0:
            return 1
        avail = self.width() if self.isVisible() else 0
        if avail <= 0:
            # Not on screen yet (an unshown page reports a meaningless
            # width): assume a typical pane rather than one item per line.
            # The first real resize re-flows against the true width.
            avail = FLOW_ASSUMED_W
        fit = int((avail + self._gap) // (self._min_col + self._gap))
        # Never more columns than there are sections: empty columns would
        # take a share of the width and leave the rest of the pane blank
        # again, which is the very thing this replaces.
        return max(1, min(int(fit), n))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def showEvent(self, event):
        super().showEvent(event)
        self._relayout()

    def _ensure_line(self, index):
        while len(self._lines) <= index:
            row = QWidget()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(self._gap)
            self._lines.append((row, lay))
            self._outer.addWidget(row)
        return self._lines[index]

    def _relayout(self, force=False):
        # The re-flow maths runs over the VISIBLE boxes; hidden ones stay
        # registered in ``_items`` (so showing one again restores its slot in
        # the original order) but are simply not placed.
        items = self.visible_sections()
        n = len(items)
        if n <= 0:
            # Everything hidden: drop the lines rather than leaving the last
            # layout's boxes stranded on screen.
            for _row, lay in self._lines:
                while lay.count():
                    lay.takeAt(0)
            return
        cols = self._col_count()
        rows = (n + cols - 1) // cols
        if not force and cols == self._cols and len(self._lines) == rows:
            return
        self._cols = cols
        # Empty every line first: a widget lives in exactly one layout, and
        # re-adding it is what moves it. The sections themselves are never
        # destroyed -- the page keeps the controls it built.
        for _row, lay in self._lines:
            while lay.count():
                lay.takeAt(0)
        for line in range(rows):
            _row, lay = self._ensure_line(line)
            in_line = items[line * cols:(line + 1) * cols]
            for w in in_line:
                lay.addWidget(w, 1)
            # Pad the last line with the slots it does not use, so its
            # sections keep the same width as every line above: the leftover
            # options stay in the left-hand columns instead of stretching
            # across a line of their own.
            for _ in range(cols - len(in_line)):
                lay.addStretch(1)
        for index in range(len(self._lines) - 1, rows - 1, -1):
            row, _lay = self._lines.pop(index)
            self._outer.removeWidget(row)
            row.hide()
            row.setParent(None)
            row.deleteLater()


def grid_row(label, widget, label_w=PILL_W):
    row = QWidget()
    row.setProperty('grid_row', True)
    hl = QHBoxLayout(row)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(ROW_SPACING)
    lab = section_label(label)
    lab.setFixedWidth(label_w)
    hl.addWidget(lab)
    hl.addWidget(widget)
    hl.addStretch()
    return row

# --- Legend / colour-bar placement controls ---------------------------------
# The choice lists live in the backend (``chart_display``) so the UI can never
# drift from what the renderer actually understands. They are read through a
# guarded import: the settings panels are importable on their own (tests, the
# headless builder), and a missing backend must degrade to a still-usable UI
# rather than an ImportError at module load.
def _chart_option_list(name, fallback):
    try:
        from chart_display import LEGEND_POSITIONS, LEGEND_ORIENTATIONS, LEGEND_ORDERS, COLORBAR_POSITIONS
        return {'legend_position': LEGEND_POSITIONS,
                'legend_orientation': LEGEND_ORIENTATIONS,
                'legend_order': LEGEND_ORDERS,
                'colorbar_position': COLORBAR_POSITIONS}[name]
    except Exception:
        return list(fallback)


def legend_position_row(layout_opts, on_change, label='Legend position',
                        width=COMPACT_W):
    """A legend-position combo holding all eight placements.

    Returns ``(row, combo)``. The combo keeps the historical attribute name
    ``_cb_legpos`` on the caller when it is assigned there, so every builder's
    existing prefill keeps working.
    """
    combo = QComboBox()
    items = _chart_option_list('legend_position',
                               ['Top Right', 'Top Left', 'Top Center',
                                'Bottom Right', 'Bottom Left', 'Bottom Center',
                                'Left', 'Right'])
    combo.addItems(items)
    want = str(layout_opts.get('legend_position', 'Top Right') or 'Top Right')
    if want not in items:
        want = 'Top Right'
    combo.setCurrentText(want)
    combo.setToolTip('Where the legend sits: in a side column, or as a\n'
                     'horizontal band above / below the plot.')
    combo.currentTextChanged.connect(lambda t: on_change(legend_position=t))
    cap_widget(combo, width)
    return grid_row(label, combo), combo


def legend_orientation_row(layout_opts, on_change, label='Legend layout',
                           width=COMPACT_W):
    """Auto / Vertical / Horizontal, the series order, and the click action.

    ``Auto`` lets the renderer choose: a side dock draws a vertical stack, a
    Top/Bottom band (or any position too wide for a column on a narrow card)
    draws a horizontal row. They are separate controls because they answer
    different questions -- which way the items run, in what order, and what a
    click on a legend entry does.
    """
    wrap = QWidget()
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(ROW_SPACING)
    orient = QComboBox()
    oitems = _chart_option_list('legend_orientation',
                                ['Auto', 'Vertical', 'Horizontal'])
    orient.addItems(oitems)
    owant = str(layout_opts.get('legend_orientation', 'Auto') or 'Auto')
    orient.setCurrentText(owant if owant in oitems else 'Auto')
    orient.setToolTip('Direction the legend items run in. Auto follows the\n'
                      'position: side docks stack, top/bottom run across.')
    cap_widget(orient, width)
    order = QComboBox()
    oitems2 = _chart_option_list('legend_order', ['Normal', 'Reversed'])
    order.addItems(oitems2)
    want2 = str(layout_opts.get('legend_order', 'Normal') or 'Normal')
    order.setCurrentText(want2 if want2 in oitems2 else 'Normal')
    order.setToolTip('Order the series appear in the legend.\n'
                     'Reversed puts the first series last.')
    cap_widget(order, width)
    lay.addWidget(orient)
    lay.addWidget(order)
    lay.addStretch()
    orient.currentTextChanged.connect(
        lambda t: on_change(legend_orientation=t))
    order.currentTextChanged.connect(lambda t: on_change(legend_order=t))
    wrap._orient = orient
    wrap._order = order
    row = grid_row(label, wrap)

    # The click action gets its OWN row: three combos plus a label do not fit a
    # 240px control column, and squeezing them in is what produced the cramped,
    # overlapping rows this pane was reported for.
    click_wrap = QWidget()
    click_lay = QHBoxLayout(click_wrap)
    click_lay.setContentsMargins(0, 0, 0, 0)
    click_lay.setSpacing(ROW_SPACING)
    click = QComboBox()
    citems = ['Toggle item', 'Toggle others', 'Do nothing']
    click.addItems(citems)
    ckeys = ['toggle', 'toggleothers', 'false']
    cwant = str(layout_opts.get('legend_itemclick', 'toggle') or 'toggle')
    click.setCurrentText(citems[ckeys.index(cwant)] if cwant in ckeys else citems[0])
    click.setToolTip('What clicking a legend entry does in the chart.\n'
                     '"Toggle others" hides every other series at once.')
    cap_widget(click, width)
    click_lay.addWidget(click)
    click_lay.addStretch()
    click.currentTextChanged.connect(
        lambda t: on_change(legend_itemclick=ckeys[citems.index(t)]))
    click_wrap._click = click
    click_row = grid_row('Click', click_wrap)
    return row, orient, order, click, click_row


_CB_ORIENTS = ('Auto', 'Vertical', 'Horizontal')


def colorbar_position_row(layout_opts, on_change, label='Colorbar pos',
                          width=COMPACT_W):
    """Colour-bar placement (Right / Left / Top / Bottom) + its orientation.

    A Top/Bottom bar is drawn horizontally under or over the plot; a Left/Right
    bar is a vertical strip. ``Auto`` orientation follows the position, so the
    second control is only needed to override that (e.g. a right-hand bar laid
    out horizontally is a valid, if unusual, choice).
    """
    wrap = QWidget()
    lay = QHBoxLayout(wrap)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(ROW_SPACING)
    pos = QComboBox()
    items = _chart_option_list('colorbar_position',
                               ['Right', 'Left', 'Top', 'Bottom'])
    pos.addItems(items)
    want = str(layout_opts.get('colorbar_position', 'Right') or 'Right')
    pos.setCurrentText(want if want in items else 'Right')
    pos.setToolTip('Where the colour range sits: as a side strip, or as a\n'
                   'horizontal bar above / below the plot.')
    cap_widget(pos, width)
    orient = QComboBox()
    orient.addItems(['Auto', 'Vertical', 'Horizontal'])
    owant = str(layout_opts.get('colorbar_orientation', 'Auto') or 'Auto')
    orient.setCurrentText(owant if owant in _CB_ORIENTS else 'Auto')
    orient.setToolTip('Auto follows the position (Top/Bottom draw across).')
    cap_widget(orient, width)
    lay.addWidget(pos)
    lay.addWidget(orient)
    lay.addStretch()
    pos.currentTextChanged.connect(
        lambda t: on_change(discrete=True, colorbar_position=t))
    orient.currentTextChanged.connect(
        lambda t: on_change(discrete=True, colorbar_orientation=t))
    wrap._pos = pos
    wrap._orient = orient
    return grid_row(label, wrap), pos, orient

def slider_with_readout(minimum, maximum, value, is_int=True, step=1, readout_width=60):
    wrap = QWidget()
    hl = QHBoxLayout(wrap)
    hl.setContentsMargins(0, 0, 0, 0)
    hl.setSpacing(6)
    sl = QSlider(Qt.Horizontal)
    if is_int:
        sl.setRange(int(minimum), int(maximum))
        sl.setValue(int(value))
        sl.setSingleStep(int(step))
    else:
        sl.setRange(int(minimum * 100), int(maximum * 100))
        sl.setValue(int(float(value) * 100))
        sl.setSingleStep(max(1, int(float(step) * 100)))
    hl.addWidget(sl, stretch=1)
    if is_int:
        spin = QSpinBox()
        spin.setRange(int(minimum), int(maximum))
        spin.setValue(int(value))
    else:
        spin = QDoubleSpinBox()
        spin.setRange(float(minimum), float(maximum))
        spin.setValue(float(value))
        try:
            spin.setSingleStep(float(step))
        except Exception:
            pass
    spin.setFixedWidth(readout_width)
    hl.addWidget(spin)
    if is_int:
        sl.valueChanged.connect(spin.setValue)
        spin.valueChanged.connect(sl.setValue)
    else:
        sl.valueChanged.connect(lambda v: spin.setValue(v / 100.0))
        try:
            spin.valueChanged.connect(lambda v: sl.setValue(int(float(v) * 100)))
        except Exception:
            pass
    wrap._as_slider = sl
    wrap._as_spin = spin
    return wrap
def _chip_style(col):
    """Stylesheet for a colour chip: the row shows the COLOUR itself (as in
    chart_designs.pdf) with the hex value kept on the tooltip."""
    c = QColor(str(col or ''))
    hexv = c.name() if c.isValid() else '#6163df'
    return ('QPushButton { background: ' + hexv + '; border: 1px solid palette(mid);'
            ' border-radius: 4px; color: transparent; }')


def color_swatch(current, on_pick):
    btn = QPushButton()
    btn.setFixedSize(56, 24)
    btn.setCursor(Qt.PointingHandCursor)
    btn.setProperty('theme_role', 'swatch')
    def _retint(col):
        try:
            # The button's text is the current colour value (kept for
            # prefill/restyle); it is painted transparently so the chip
            # itself is what the user sees.
            btn.setText(str(col))
            btn.setToolTip(str(col))
            btn.setStyleSheet(_chip_style(col))
        except Exception:
            pass
    btn._retint = _retint
    _retint(current)
    try:
        btn.clicked.connect(lambda: _pick())
    except Exception:
        pass
    def _pick():
        try:
            start = str(btn.text() or current or '#6163df')
            col = QColorDialog.getColor(QColor(start))
            if col.isValid():
                btn._retint(col.name())
                on_pick(col.name())
        except Exception:
            pass
    btn._pick = _pick
    return btn
def live_preview_label(text='Preview - 123 Inter'):
    lbl = QLabel(text)
    try:
        lbl.setStyleSheet(label_style())
    except Exception:
        pass
    lbl.setProperty('theme_role', 'label')
    return lbl
def apply_preview_font(lbl, family, style, size=13):
    f = QFont(family, size)
    s = (style or 'Normal').lower()
    f.setBold('bold' in s)
    it = ('italic' in s) or ('oblique' in s)
    try:
        f.setItalic(it)
    except Exception:
        pass
    lbl.setFont(f)
    lbl.setText(f'Preview - 123 {family}')
class AnalysisSettingsBase(QWidget):
    values_changed = Signal(dict)
    generate_requested = Signal()
    built = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self._num_cols = []
        self._cat_cols = []
        self._dt_cols = []
        self._palette_names = []
        self._all_cols = []
        self._trace_names = []
        self._group_cols = []
        self.NONE = 'None'
        self._dirty = False
        self._pending = {}
        self._layout_opts = {}
        self._typo_opts = {}
        self._suppress = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
    def set_context(self, num_cols=None, cat_cols=None, dt_cols=None, palette_names=None, all_cols=None, trace_names=None, group_cols=None, dim_cols=None):
        self._num_cols = list(num_cols or [])
        self._cat_cols = list(cat_cols or [])
        self._dt_cols = list(dt_cols or [])
        self._palette_names = list(palette_names or [])
        self._all_cols = list(all_cols or [])
        self._trace_names = list(trace_names or [])
        if dim_cols is not None:
            # Explicit dimension pool from the page (text + datetime, minus
            # whatever the user classified as a metric).
            self._group_cols = list(dim_cols)
        elif group_cols is not None:
            self._group_cols = list(group_cols)
        else:
            # Fall back to the dimension families we were handed, NOT just
            # the text ones: a datetime column is a valid grouping key and
            # must not be missing from "Group by" simply because it is not
            # object dtype.
            self._group_cols = list(self._cat_cols) + [
                c for c in self._dt_cols if c not in self._cat_cols]
    def _ensure_context(self):
        pass
    def _build(self):
        pass
    def build_generate(self):
        return QWidget()
    def build_layout(self):
        return QWidget()
    def build_typography(self):
        return QWidget()
    def build_action_row(self, on_generate=None):
        if getattr(self, 'btn_action', None) is not None:
            try:
                if on_generate is not None:
                    self.btn_action.clicked.disconnect()
                    self.btn_action.clicked.connect(on_generate)
            except Exception:
                pass
            return self.btn_action
        from PySide6.QtWidgets import QHBoxLayout as _HL
        row = QWidget()
        hl = _HL(row)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addStretch()
        btn = QPushButton(CTA_TEXT)
        btn.setProperty('kind', 'primary')
        if on_generate is not None:
            btn.clicked.connect(on_generate)
        else:
            btn.clicked.connect(lambda: self.generate_requested.emit())
        hl.addWidget(btn)
        self.btn_action = btn
        return row
    def action_button(self):
        return getattr(self, 'btn_action', None)
    def set_action_state(self, text=None, enabled=True):
        btn = getattr(self, 'btn_action', None)
        if btn is None:
            return
        if text is not None:
            btn.setText(text)
        btn.setEnabled(bool(enabled))
    def _mark_dirty(self, emit=True):
        self._dirty = True
        if emit:
            try:
                self.values_changed.emit(dict(self._pending))
            except Exception:
                pass
    def collect_values(self):
        return {}
    def collect_layout(self):
        return dict(self._layout_opts)
    def collect_typography(self):
        return dict(self._typo_opts)
    def prefill(self, values):
        pass
    def prefill_layout(self, values):
        pass
    def prefill_typography(self, values):
        pass
    def dirty(self):
        return self._dirty
    def reset_dirty(self):
        self._dirty = False
    def _mask_rows(self, root, labels):
        """Delete the rows whose label text is in *labels*.

        Shared by every type that inherits the template and then offers LESS
        than it (see ``CorrelationSettings`` / ``CategoricalSettings`` and
        ``extra/CHART_IMPLEMENTATION_RULES.md``). Removing the row outright
        instead of hiding it takes the control out of the layout entirely, so
        it cannot be reached by tab order, by a stray focus change, or by a
        later prefill.

        Both row styles are handled: a ``grid_row`` (label + widget, matched
        via the section-role QLabel) and a ``_check_row`` checkbox (matched on
        the checkbox text itself -- "Show border" has no separate label).
        """
        wanted = {str(t).strip() for t in labels}
        seen = set()
        for lbl in list(root.findChildren(QLabel)):
            try:
                if (str(lbl.property('theme_role')) != 'section'
                        or lbl.text().strip() not in wanted):
                    continue
                row = lbl.parentWidget()
                if row is None or id(row) in seen:
                    continue
                seen.add(id(row))
                self._detach_row(row)
            except Exception:
                continue
        for box in list(root.findChildren(QCheckBox)):
            try:
                if box.text().strip() not in wanted:
                    continue
                row = box.parentWidget()
                if row is None or id(row) in seen:
                    continue
                seen.add(id(row))
                self._detach_row(row)
            except Exception:
                continue
    @staticmethod
    def _detach_row(row):
        """Unparent *row* from its layout and schedule it for deletion."""
        lay = row.layout()
        if lay is not None:
            while lay.count():
                item = lay.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()
        row.setParent(None)
        row.deleteLater()

    # -- Shared layout-column access ---------------------------------------
    # A builder that inherits the template's Layout tab and then ADDS its own
    # type-specific rows must put them in the column that already exists, not in
    # a third one. These two helpers let it do that without duplicating
    # tree-walking per type.

    def _option_columns(self, root):
        """The Layout tab's option columns, left to right.

        Located by the marker the template puts on each column (the ``grid_row``
        / check-row widgets that hold them are children of the column itself),
        and returned innermost-column-first would be fragile, so they come back
        in visual order: the first is the left-hand column.
        """
        out = []
        try:
            for w in root.findChildren(QWidget):
                if w.property('option_column'):
                    out.append(w)
        except Exception:
            return []
        # ``findChildren`` is a depth-first walk, which for this tree is already
        # left-to-right, but sort on x to be certain rather than assumed.
        def _x(w):
            try:
                return w.mapTo(root, QPoint(0, 0)).x()
            except Exception:
                return 0
        out.sort(key=_x)
        return out

    def _layout_option_column(self, root):
        """The right-hand Layout option column -- where extra rows belong."""
        cols = self._option_columns(root)
        return cols[-1] if cols else None

    def _option_insert_index(self, column):
        """Row index that appends just above a column's trailing stretch.

        The shared columns end with ``addStretch()``, so inserting at the
        spacer keeps the new rows grouped with the other options rather than
        dropping them below the filler.
        """
        lay = column.layout()
        for i in range(lay.count()):
            if lay.itemAt(i).spacerItem() is not None:
                return i
        return lay.count()

    def _add_option_row(self, root, row, column_index=-1):
        """Insert *row* into the Layout tab's option *column_index*.

        Returns True when it landed. Used by every builder that extends the
        template's Layout tab with its own type-specific options.
        """
        cols = self._option_columns(root)
        if not cols:
            return False
        try:
            column = cols[column_index]
            column.layout().insertWidget(self._option_insert_index(column), row)
            return True
        except Exception:
            log.debug("could not add the option row to the layout column",
                      exc_info=True)
            return False

def restyle_tree(root):
    try:
        for w in root.findChildren(QWidget):
            try:
                role = w.property('theme_role')
            except Exception:
                role = None
            if role == 'section':
                w.setStyleSheet(group_label_style())
            elif role == 'label':
                w.setStyleSheet(label_style())
            elif role == 'swatch':
                try:
                    fn = getattr(w, '_retint', None)
                    if callable(fn):
                        fn(w.text())
                except Exception:
                    pass
    except Exception:
        pass
