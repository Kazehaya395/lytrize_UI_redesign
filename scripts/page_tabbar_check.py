import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO); sys.path.insert(0, os.path.join(REPO, "backend"))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from desktop.pages.dashboard_page import _PageTabBar

app = QApplication.instance() or QApplication([])
bar = _PageTabBar()
got = []
bar.page_selected.connect(lambda pid: got.append(("sel", pid)))
bar.page_added.connect(lambda: got.append(("add",)))
bar.page_renamed.connect(lambda pid: got.append(("rename", pid)))
bar.page_deleted.connect(lambda pid: got.append(("delete", pid)))
bar.set_pages([{"id": "overview", "name": "Overview"},
               {"id": "finance", "name": "Finance"}], "overview")
bar.show()
app.processEvents()
assert [b.text() for b in bar._tab_buttons.values()] == ["Overview", "Finance"]
assert bar._tab_buttons["overview"].isChecked()
QTest.mouseClick(bar._tab_buttons["finance"], Qt.LeftButton)
assert got == [("sel", "finance")], got
bar.set_pages([{"id": "overview", "name": "Overview"},
               {"id": "finance", "name": "Finance"}], "finance")
app.processEvents()
assert bar._tab_buttons["finance"].isChecked()
QTest.mouseClick(bar._add, Qt.LeftButton)
assert ("add",) in got
# right-click menu on the finance tab -> rename / delete. The modal menu
# exec cannot run offscreen, so swap the module's QMenu for a fake that
# records its actions and "chooses" one.
import desktop.pages.dashboard_page as dp_mod

class _FakeAction:
    def __init__(self, text):
        self._text = text
        self._enabled = True

    def text(self):
        return self._text

    def setEnabled(self, value):
        self._enabled = bool(value)

class _FakeMenu:
    choose = None   # label of the action to "pick", set per probe
    last = None

    def __init__(self, parent=None):
        self._actions = []
        _FakeMenu.last = self

    def addAction(self, text):
        action = _FakeAction(text)
        self._actions.append(action)
        return action

    def actions(self):
        return list(self._actions)

    def exec(self, *_a, **_k):
        return next((a for a in self._actions if a.text() == _FakeMenu.choose),
                    None)

real_qmenu = dp_mod.QMenu
dp_mod.QMenu = _FakeMenu
btn = bar._tab_buttons["finance"]
_FakeMenu.choose = "Rename…"
bar._on_context_menu(btn.pos() + btn.rect().center())
assert ("rename", "finance") in got, got
_FakeMenu.choose = "Delete page"
bar._on_context_menu(btn.pos() + btn.rect().center())
assert ("delete", "finance") in got, got
# delete disabled when only one page remains
bar.set_pages([{"id": "overview", "name": "Overview"}], "overview")
app.processEvents()
_FakeMenu.choose = "Delete page"
bar._on_context_menu(bar._tab_buttons["overview"].pos() + bar._tab_buttons["overview"].rect().center())
assert _FakeMenu.last._actions[1]._enabled is False
dp_mod.QMenu = real_qmenu
print("tab bar OK:", got)
