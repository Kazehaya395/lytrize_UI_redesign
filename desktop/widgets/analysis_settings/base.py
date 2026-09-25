"""Shared flat-section helpers + 3-tab builder contract (no cards)."""
from __future__ import annotations
from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPalette, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QApplication, QColorDialog, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QLineEdit, QPushButton, QSizePolicy, QSlider, QSpinBox, QStyle, QStyledItemDelegate, QStyleOptionButton, QStyleOptionViewItem, QVBoxLayout, QWidget)
from desktop.theme import group_label_style, label_style, resolve_color, input_style, PAGE_MARGINS
def _fallback(mode):
    return None
CTA_TEXT = '\u25b6 Generate Charts'
CTA_TEXT_EDIT = '\U0001f4be Update Chart'
COMPACT_W = 240
PAGE_SPACING = 10
ROW_SPACING = 6
HINT_GAP = 4
SECTION_GAP = 14
PILL_W = 128
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
    PLACEHOLDER = 'Dropdown with checkboxes; click to expand'
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
    wrap = QWidget()
    wl = QVBoxLayout(wrap)
    wl.setContentsMargins(12, 4, 12, 4)
    wl.setSpacing(HINT_GAP)
    wl.addWidget(section_label(title))
    if hint:
        wl.addWidget(hint_label(hint))
    wl.addWidget(body)
    return wrap
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
    def set_context(self, num_cols=None, cat_cols=None, dt_cols=None, palette_names=None, all_cols=None, trace_names=None, group_cols=None):
        self._num_cols = list(num_cols or [])
        self._cat_cols = list(cat_cols or [])
        self._dt_cols = list(dt_cols or [])
        self._palette_names = list(palette_names or [])
        self._all_cols = list(all_cols or [])
        self._trace_names = list(trace_names or [])
        if group_cols is None:
            self._group_cols = list(cat_cols or [])
        else:
            self._group_cols = list(group_cols)
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
