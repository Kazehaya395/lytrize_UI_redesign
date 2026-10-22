"""desktop/pages/profile_page.py -- Profile page (Qt).

Replaces the Streamlit ``page_profile()`` with a native Qt page that shows
the local guest user profile and provides backup/restore of sessions.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
from pathlib import Path
import tempfile
from typing import Optional
import zipfile

from PySide6.QtCore import Qt, Signal, Slot, QRunnable, QThreadPool, QObject
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QFrame, QGroupBox, QFormLayout,
)

from desktop.theme import PAGE_MARGINS
from desktop.session_bridge import SessionBridge
from services.database_service import (
    get_or_create_guest_user, export_sessions_to_dict,
    import_sessions_from_dict, invalidate_sessions_cache,
)

log = logging.getLogger(__name__)

_BACKUP_FORMAT = "lytrize-session-package"
_BACKUP_VERSION = 2
_MAX_BACKUP_UNPACKED = 8 * 1024 * 1024 * 1024


class _BackupSignals(QObject):
    exported = Signal(str, int, object)  # path, session count, missing datasets
    restored = Signal(object, object)    # sessions, warnings
    failed = Signal(str, str)            # operation, error


class _BackupTask(QRunnable):
    """Run archive compression, hashing, or extraction away from the UI."""

    def __init__(self, operation: str, signals: _BackupSignals, *,
                 path: Path, sessions: list[dict] | None = None) -> None:
        super().__init__()
        self.operation = operation
        self.signals = signals
        self.path = path
        self.sessions = sessions

    def run(self) -> None:
        try:
            if self.operation == "export":
                sessions = self.sessions or []
                missing = _package_backup(self.path, sessions)
                self.signals.exported.emit(str(self.path), len(sessions), missing)
            else:
                sessions, warnings = _read_backup(self.path)
                self.signals.restored.emit(sessions, warnings)
        except Exception as exc:
            log.exception("Backup %s failed", self.operation)
            self.signals.failed.emit(self.operation, str(exc))


def _package_backup(path: Path, sessions: list[dict]) -> list[str]:
    """Write sessions and available Parquet snapshots as one portable package.

    Large datasets remain separate ZIP members rather than being base64
    expanded into the session JSON. Images are already portable data URIs in
    the saved style record and are checksum-covered with the manifest.
    Returns dataset URIs that could not be included.
    """
    from services.session_bundle import _bundle_dir

    datasets = _bundle_dir()
    assets: list[dict] = []
    missing: set[str] = set()
    files: dict[str, Path] = {}
    for session in sessions:
        uri = str(session.get("dataset_uri") or "").strip()
        if not uri:
            continue
        name = Path(uri).name
        source = datasets / name
        if not name or name in {".", ".."} or not source.is_file():
            missing.add(uri)
            continue
        archive_name = f"datasets/{name}"
        if archive_name not in files:
            files[archive_name] = source
            digest = hashlib.sha256()
            size = 0
            with source.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            assets.append({"uri": name, "path": archive_name,
                           "sha256": digest.hexdigest(), "size": size})

    manifest = {
        "app": "lytrize", "format": _BACKUP_FORMAT,
        "version": _BACKUP_VERSION,
        "exported_at": __import__("datetime").datetime.now().isoformat(),
        "sessions": sessions, "assets": assets,
        "missing_datasets": sorted(missing),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as archive:
        archive.writestr("manifest.json", json.dumps(
            manifest, ensure_ascii=False, separators=(",", ":"),
            default=str).encode("utf-8"))
        for archive_name, source in files.items():
            archive.write(source, archive_name)
    return sorted(missing)


def _read_backup(path: Path) -> tuple[list[dict], list[str]]:
    """Read and validate a package, with backward compatibility for JSON v1.

    Package contents are staged and all checksums verified before any session
    row is imported. Dataset files are then installed atomically into Lytrize's
    local bundle directory. A legacy JSON backup remains restorable, but its
    machine-local dataset references are cleared because it cannot contain the
    referenced Parquet files.
    """
    if not zipfile.is_zipfile(path):
        data = json.loads(path.read_text(encoding="utf-8"))
        sessions = data.get("sessions", []) if isinstance(data, dict) else data
        if not isinstance(sessions, list) or not all(
                isinstance(item, dict) for item in sessions):
            raise ValueError("Backup must contain a list of session records.")
        for item in sessions:
            if item.get("dataset_uri"):
                item["dataset_uri"] = ""
                item["dataset_hash"] = ""
        return sessions, ["Legacy JSON backup: dataset files were not included."]

    with zipfile.ZipFile(path, "r") as archive:
        infos = archive.infolist()
        if sum(max(0, info.file_size) for info in infos) > _MAX_BACKUP_UNPACKED:
            raise ValueError("Backup exceeds the 8 GiB expanded-size limit.")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)) or "manifest.json" not in names:
            raise ValueError("Backup package has duplicate entries or no manifest.")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if (not isinstance(manifest, dict)
                or manifest.get("format") != _BACKUP_FORMAT
                or manifest.get("version") != _BACKUP_VERSION):
            raise ValueError("Unsupported Lytrize backup package version.")
        sessions = manifest.get("sessions")
        assets = manifest.get("assets", [])
        if (not isinstance(sessions, list)
                or not all(isinstance(item, dict) for item in sessions)
                or not isinstance(assets, list)):
            raise ValueError("Backup manifest has invalid session or asset data.")

        from services.session_bundle import _bundle_dir
        installs: list[tuple[Path, str, str]] = []
        staged_dir = tempfile.TemporaryDirectory(prefix="lytrize-restore-")
        try:
            staging = Path(staged_dir.name)
            archived = set(names)
            mapped_uris: set[str] = set()
            for asset in assets:
                if not isinstance(asset, dict):
                    raise ValueError("Backup contains an invalid asset record.")
                uri = str(asset.get("uri") or "")
                member = str(asset.get("path") or "")
                digest_expected = str(asset.get("sha256") or "")
                if (not uri or Path(uri).name != uri or not member.startswith("datasets/")
                        or Path(member).name != uri or member not in archived
                        or uri in mapped_uris or len(digest_expected) != 64):
                    raise ValueError("Backup contains an unsafe dataset asset path.")
                mapped_uris.add(uri)
                staged_file = staging / uri
                digest = hashlib.sha256()
                size = 0
                with archive.open(member, "r") as source, staged_file.open("wb") as target:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        size += len(chunk)
                        if size > int(asset.get("size", -1)):
                            raise ValueError(f"Dataset asset size mismatch: {uri}")
                        digest.update(chunk)
                        target.write(chunk)
                if (size != int(asset.get("size", -1))
                        or digest.hexdigest() != digest_expected):
                    raise ValueError(f"Dataset asset checksum mismatch: {uri}")
                installs.append((staged_file, uri, digest_expected))

            bundle_dir = _bundle_dir()
            for staged_file, uri, expected_hash in installs:
                destination = bundle_dir / uri
                current_hash = ""
                if destination.is_file():
                    current = hashlib.sha256()
                    with destination.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                            current.update(chunk)
                    current_hash = current.hexdigest()
                if current_hash != expected_hash:
                    os.replace(staged_file, destination)

            unavailable = set(str(uri) for uri in manifest.get("missing_datasets", []))
            for session in sessions:
                uri = str(session.get("dataset_uri") or "")
                if uri and Path(uri).name not in mapped_uris:
                    unavailable.add(uri)
                    session["dataset_uri"] = ""
                    session["dataset_hash"] = ""
            warnings = ([f"Dataset unavailable: {uri}" for uri in sorted(unavailable)])
            return sessions, warnings
        finally:
            staged_dir.cleanup()


class ProfilePage(QWidget):
    """Profile page: user info and backup/restore."""

    navigate_requested = Signal(str)

    def __init__(self, bridge: SessionBridge, parent=None) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self._backup_signals = _BackupSignals(self)
        self._backup_signals.exported.connect(self._on_backup_exported)
        self._backup_signals.restored.connect(self._on_backup_restored)
        self._backup_signals.failed.connect(self._on_backup_failed)
        self._backup_busy = False

        self._build_ui()
        self._connect_signals()
        self._refresh_user_info()

    # -- UI construction ----------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*PAGE_MARGINS)
        root.setSpacing(12)

        # -- Header --
        header = QHBoxLayout()
        title = QLabel("👤 Profile")
        title.setProperty("class", "pageTitle")
        header.addWidget(title)
        header.addStretch()
        root.addLayout(header)

        # -- User info card --
        user_card = QFrame()
        user_card.setObjectName("user_card")
        # (background themed by global QSS via objectName "user_card")
        user_layout = QVBoxLayout(user_card)
        user_layout.setContentsMargins(16, 16, 16, 16)
        user_layout.setSpacing(8)

        self.lbl_username = QLabel("")
        self.lbl_username.setProperty("class", "cardTitle"); self.lbl_username.setStyleSheet("font-size: 16px; font-weight: 700;")
        user_layout.addWidget(self.lbl_username)

        self.lbl_session_count = QLabel("")
        self.lbl_session_count.setProperty("class", "faint"); self.lbl_session_count.setStyleSheet("font-size: 12px;")
        user_layout.addWidget(self.lbl_session_count)

        root.addWidget(user_card)

        # -- Backup / Restore section --
        backup_group = QGroupBox("💾 Backup & Restore")
        # (GroupBox themed globally via QSS)
        backup_layout = QVBoxLayout(backup_group)
        backup_layout.setContentsMargins(16, 12, 16, 16)
        backup_layout.setSpacing(8)

        backup_desc = QLabel(
        "Export saved dashboards, datasets, and their settings into one "
        "portable Lytrize package, or restore a package or older JSON backup."
        )
        backup_desc.setProperty("class", "muted"); backup_desc.setStyleSheet("font-size: 12px;")
        backup_desc.setWordWrap(True)
        backup_layout.addWidget(backup_desc)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_backup = QPushButton("⬇️ Export Backup")
        self.btn_backup.setProperty("kind", "primary")
        btn_row.addWidget(self.btn_backup)

        self.btn_restore = QPushButton("⬆️ Restore Backup")
        self.btn_restore.setProperty("kind", "ghost")
        btn_row.addWidget(self.btn_restore)
        # Buttons collapse to their content size; the row's right side stays
        # empty on purpose (UI feedback: half-page-wide buttons looked bloated).
        btn_row.addStretch(1)

        backup_layout.addLayout(btn_row)
        root.addWidget(backup_group)

        # -- Data location info --
        data_group = QGroupBox("📁 Data Location")
        # (GroupBox themed globally via QSS)
        data_layout = QVBoxLayout(data_group)
        data_layout.setContentsMargins(16, 12, 16, 16)

        data_desc = QLabel(
            "All data is stored locally on this device. No data is sent to any server."
        )
        data_desc.setProperty("class", "muted"); data_desc.setStyleSheet("font-size: 12px;")
        data_desc.setWordWrap(True)
        data_layout.addWidget(data_desc)

        self.lbl_db_path = QLabel("")
        self.lbl_db_path.setProperty("class", "faint"); self.lbl_db_path.setStyleSheet("font-size: 11px;")
        self.lbl_db_path.setWordWrap(True)
        data_layout.addWidget(self.lbl_db_path)

        root.addWidget(data_group)
        root.addStretch()

    # -- Signal connections -------------------------------------------------

    def _connect_signals(self) -> None:
        self.btn_backup.clicked.connect(self._on_backup)
        self.btn_restore.clicked.connect(self._on_restore)

    # -- User info ----------------------------------------------------------

    def _refresh_user_info(self) -> None:
        """Refresh the displayed user information."""
        user_id = self.bridge.get("user_id")
        username = self.bridge.get("username", "")

        self.lbl_username.setText(username or "User")

        from services.database_service import get_user_sessions, DB_PATH
        try:
            count = len(get_user_sessions(user_id)) if user_id else 0
        except Exception:
            count = 0
        self.lbl_session_count.setText(
            f"{count} saved session{'s' if count != 1 else ''}"
        )
        self.lbl_db_path.setText(f"Database: {DB_PATH}")

    # -- Backup / Restore ---------------------------------------------------

    def _on_backup(self) -> None:
        """Export all sessions and local dataset snapshots to a package."""
        user_id = self.bridge.get("user_id")
        if not user_id:
            QMessageBox.warning(self, "No User", "No user session found.")
            return

        username = self.bridge.get("username", "guest")
        try:
            sessions = export_sessions_to_dict(user_id, username)
            if not sessions:
                QMessageBox.information(self, "No Sessions",
                                        "No saved sessions to export.")
                return

            file_path, _ = QFileDialog.getSaveFileName(
                self, "Export Backup", str(Path.home() / "lytrize_backup.lybackup"),
                "Lytrize Backup Package (*.lybackup)",
            )
            if not file_path:
                return
            target = Path(file_path)
            if target.suffix.lower() != ".lybackup":
                target = target.with_suffix(".lybackup")
            self._set_backup_busy(True)
            task = _BackupTask("export", self._backup_signals,
                               path=target, sessions=sessions)
            QThreadPool.globalInstance().start(task)
        except Exception as exc:
            log.error("Backup failed: %s", exc)
            QMessageBox.critical(self, "Backup Error", str(exc))

    @Slot(str, int, object)
    def _on_backup_exported(self, path: str, count: int, missing) -> None:
        self._set_backup_busy(False)
        note = (f"\n{len(missing)} dataset reference(s) were unavailable "
                "and could not be included." if missing else "")
        QMessageBox.information(self, "Backup Complete",
                                f"Exported {count} sessions and their available "
                                f"datasets to:\n{path}{note}")

    def _on_restore(self) -> None:
        """Restore sessions from a JSON backup file."""
        user_id = self.bridge.get("user_id")
        if not user_id:
            QMessageBox.warning(self, "No User", "No user session found.")
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Restore Backup", str(Path.home()),
            "Lytrize Backup Package (*.lybackup);;Legacy JSON Backup (*.json)",
        )
        if not file_path:
            return

        self._set_backup_busy(True)
        task = _BackupTask("restore", self._backup_signals, path=Path(file_path))
        QThreadPool.globalInstance().start(task)

    @Slot(object, object)
    def _on_backup_restored(self, sessions, warnings) -> None:
        self._set_backup_busy(False)
        if not sessions:
            QMessageBox.warning(self, "Invalid Backup",
                                "No sessions found in the backup file.")
            return
        user_id = self.bridge.get("user_id")
        try:
            imported, updated, skipped = import_sessions_from_dict(user_id, sessions)
            invalidate_sessions_cache()
            msg = f"Imported: {imported}\nUpdated: {updated}"
            if skipped:
                msg += f"\nSkipped: {len(skipped)}"
            if warnings:
                msg += "\n\n" + "\n".join(warnings[:8])
                if len(warnings) > 8:
                    msg += f"\n…and {len(warnings) - 8} more unavailable datasets."
            QMessageBox.information(self, "Restore Complete", msg)
        except Exception as exc:
            log.exception("Restoring sessions into the local database failed")
            QMessageBox.critical(self, "Restore Error", str(exc))

    @Slot(str, str)
    def _on_backup_failed(self, operation: str, error: str) -> None:
        self._set_backup_busy(False)
        title = "Backup Error" if operation == "export" else "Restore Error"
        QMessageBox.critical(self, title, error)

    def _set_backup_busy(self, busy: bool) -> None:
        self._backup_busy = bool(busy)
        self.btn_backup.setEnabled(not busy)
        self.btn_restore.setEnabled(not busy)
        self.btn_backup.setText("Working…" if busy else "⬇️ Export Backup")
        self.btn_restore.setText("Working…" if busy else "⬆️ Restore Backup")

    # -- Public API ----------------------------------------------------------

    def refresh(self) -> None:
        """Refresh the page when navigated to."""
        self._refresh_user_info()
