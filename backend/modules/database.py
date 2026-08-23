"""modules/database.py -- All database operations for Lytrize."""


import json
import time
import threading
import uuid
import os
import getpass
import hashlib
import datetime
import logging
from contextlib import contextmanager
from typing import Optional


log = logging.getLogger(__name__)


def _ttl_cache(ttl_seconds: float):
    """Small thread-safe TTL memoisation decorator.

    Headless replacement for ``@st.cache_data(ttl=...)``. Exposes
    ``.clear()`` on the wrapped function so existing call sites
    (``get_user_sessions.clear()``) keep working unchanged.
    """
    def decorator(fn):
        store: dict = {}
        lock = threading.Lock()

        def wrapped(*args, **kwargs):
            key = (args, tuple(sorted(kwargs.items())))
            now = time.monotonic()
            with lock:
                hit = store.get(key)
                if hit is not None and (now - hit[0]) < ttl_seconds:
                    return hit[1]
            value = fn(*args, **kwargs)
            with lock:
                store[key] = (now, value)
            return value

        def clear():
            with lock:
                store.clear()

        wrapped.clear = clear
        wrapped.__name__ = getattr(fn, "__name__", "wrapped")
        return wrapped
    return decorator




import pathlib as _pathlib
_default_db = str(
    _pathlib.Path.home() / ".local" / "share" / "lytrize" / "lytrize.db"
)
DB_PATH = os.environ.get("LYTRIZE_DB_PATH") or _default_db




def _connect():
    """Return a fresh SQLite DB connection with safe pragmas."""
    import sqlite3
    db_path = _pathlib.Path(DB_PATH)


    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        log.exception("_connect: failed to create parent directory %s", db_path.parent)


    if not db_path.parent.is_dir():
        raise OSError(
            f"Database parent directory does not exist or is not a directory: "
            f"{db_path.parent}"
        )


    if db_path.is_dir():
        try:
            import shutil
            shutil.rmtree(db_path)
            log.warning("_connect: removed stale directory at DB_PATH %s", db_path)
        except Exception:
            log.exception("_connect: failed to remove stale directory at %s", db_path)
            raise


    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA mmap_size=134217728")
    return conn




@contextmanager
def _db():
    """Context manager that opens a connection, commits on success, and rolls back on error."""
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception as exc:
            logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
            pass
        raise
    finally:
        conn.close()




def _ph(sql: str) -> str:
    """SQLite placeholder passthrough."""
    return sql




def _last_id(cursor) -> int:
    """Return the last auto-generated row ID from a cursor."""
    return cursor.lastrowid




def _execute(conn, query: str, params=()):
    """Execute a SQL query and return the cursor."""
    cur = conn.cursor()
    cur.execute(_ph(query), params)
    return cur




def _execute_fetchone(conn, query: str, params=()):
    """Execute a SQL query and return the first row."""
    cur = conn.cursor()
    cur.execute(_ph(query), params)
    row = cur.fetchone()
    cur.close()
    return row




def _execute_fetchall(conn, query: str, params=()):
    """Execute a SQL query and return all rows."""
    cur = conn.cursor()
    cur.execute(_ph(query), params)
    rows = cur.fetchall()
    cur.close()
    return rows




_GUEST_USERNAME = "__lytrize_guest__"
_GUEST_EMAIL    = "guest@local.invalid"


def _os_username() -> str:
    """Return the current OS username, for display in the Profile page.

    Falls back to a generic label if the OS lookup fails (e.g. some
    sandboxed/container environments without a resolvable user entry).
    """
    try:
        name = getpass.getuser()
        return name if name else "User"
    except Exception:
        return "User"


_ALLOWED_USER_COLS = frozenset({
    "id", "username", "email", "password_hash",
    "created_at", "is_guest", "uuid",
})




def _column_exists(conn, table: str, column: str) -> bool:
    """Return True when a table already contains the given column."""
    try:
        c = conn.cursor()
        c.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in c.fetchall())
    except Exception:
        return False




def _ensure_index(conn, index_sql: str) -> None:
    """Create an index if it does not already exist."""
    try:
        conn.cursor().execute(index_sql)
    except Exception as exc:
        logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
        pass




def _guest_row_id(conn) -> Optional[int]:
    """Return the permanent local guest user ID if it exists."""
    c = conn.cursor()
    if _column_exists(conn, "users", "is_guest"):
        c.execute(_ph("SELECT id FROM users WHERE is_guest=? LIMIT 1"), (True,))
        row = c.fetchone()
        if row:
            return row[0]
    c.execute(_ph("SELECT id FROM users WHERE username=? LIMIT 1"), (_GUEST_USERNAME,))
    row = c.fetchone()
    return row[0] if row else None




def init_db() -> None:
    """Create all required tables. Safe to call every startup (IF NOT EXISTS)."""
    conn = _connect()
    try:
        c = conn.cursor()


        c.execute("""CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            email         TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_guest      INTEGER DEFAULT 0,
            uuid          TEXT UNIQUE
        )""")


        c.execute("""CREATE TABLE IF NOT EXISTS sessions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL,
            session_uuid        TEXT UNIQUE,
            session_name        TEXT NOT NULL,
            file_name           TEXT,
            rows_count          INTEGER,
            cols_count          INTEGER,
            analysis_types      TEXT,
            charts_json         TEXT,
            dashboard_title     TEXT DEFAULT '',
            kpis_json           TEXT DEFAULT '[]',
            layout_mode         TEXT DEFAULT 'portrait',
            grid_cols_n         INTEGER DEFAULT 2,
            grid_order_json     TEXT DEFAULT '[]',
            grid_fullwidth_json TEXT DEFAULT '{}',
            export_text_json    TEXT DEFAULT '{}',
            export_colours_json TEXT DEFAULT '{}',
            source              TEXT DEFAULT 'local',
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""")


        c.execute("""CREATE TABLE IF NOT EXISTS user_activity (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            session_id    INTEGER,
            action_type   TEXT NOT NULL,
            action_detail TEXT,
            ts            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")


        c.execute("""CREATE TABLE IF NOT EXISTS draft_sessions (
            user_id              INTEGER PRIMARY KEY,
            page                 TEXT DEFAULT 'home',
            charts_json          TEXT DEFAULT '[]',
            file_name            TEXT DEFAULT '',
            editing_session_id   INTEGER,
            editing_session_name TEXT,
            editing_file_name    TEXT DEFAULT '',
            dashboard_title      TEXT DEFAULT '',
            kpis_json            TEXT DEFAULT '[]',
            chart_meta_json      TEXT DEFAULT '{}',
            layout_mode           TEXT DEFAULT 'portrait',
            updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""")


        # Legacy columns that may be missing on older installs. Each entry is
        # only ALTERed in when it's actually absent (checked once via PRAGMA
        # table_info per table), instead of speculatively firing every ALTER
        # every startup and swallowing the resulting OperationalError when a
        # column already exists -- that pattern deliberately raised and
        # caught up to 17 exceptions on every single app launch.
        _legacy_columns = {
            "users": [
                ("is_guest", "INTEGER DEFAULT 0"),
                ("uuid",     "TEXT"),
            ],
            "sessions": [
                ("session_name",        "TEXT NOT NULL DEFAULT ''"),
                ("file_name",           "TEXT"),
                ("rows_count",          "INTEGER"),
                ("cols_count",          "INTEGER"),
                ("analysis_types",      "TEXT"),
                ("charts_json",         "TEXT"),
                ("session_uuid",        "TEXT"),
                ("source",              "TEXT DEFAULT 'local'"),
                ("dashboard_title",     "TEXT DEFAULT ''"),
                ("kpis_json",           "TEXT DEFAULT '[]'"),
                ("layout_mode",         "TEXT DEFAULT 'portrait'"),
                ("grid_cols_n",         "INTEGER DEFAULT 2"),
                ("grid_order_json",     "TEXT DEFAULT '[]'"),
                ("grid_fullwidth_json", "TEXT DEFAULT '{}'"),
                ("updated_at",          "TIMESTAMP"),
                ("export_text_json",    "TEXT DEFAULT '{}'"),
                ("export_colours_json", "TEXT DEFAULT '{}'"),
            ],
        }

        for table, columns in _legacy_columns.items():
            try:
                existing = {row[1] for row in c.execute(f"PRAGMA table_info({table})")}
            except Exception:
                log.exception("init_db: failed reading schema for table %s", table)
                continue
            for col, typedef in columns:
                if col not in existing:
                    try:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
                    except Exception:
                        log.exception(
                            "init_db: failed to add missing column %s.%s", table, col
                        )


        try:
            c.execute("SELECT COUNT(*) FROM users WHERE username=?", (_GUEST_USERNAME,))
            if c.fetchone()[0] == 0:
                c.execute(
                    "INSERT INTO users "
                    "(username, email, password_hash, is_guest, uuid) "
                    "VALUES (?,?,?,?,?)",
                    (_GUEST_USERNAME, _GUEST_EMAIL, _hash(uuid.uuid4().hex), 1, uuid.uuid4().hex),
                )
        except Exception:
            log.exception("init_db: failed to seed guest user")


        try:
            c.execute(
                "SELECT id, session_name, created_at FROM sessions "
                "WHERE session_uuid IS NULL OR session_uuid='' ORDER BY id"
            )
            for sid, sname, created_at in c.fetchall():
                seed = f"{sid}:{sname}:{created_at}"
                c.execute(
                    "UPDATE sessions SET session_uuid=? WHERE id=?",
                    (uuid.uuid5(uuid.NAMESPACE_URL, seed).hex, sid),
                )
        except Exception:
            log.exception("init_db: failed to backfill session UUIDs")


        try:
            c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
            if not c.fetchone():
                raise Exception("'users' table not found in sqlite_master")
        except Exception:
            log.exception("init_db: users table missing or corrupt after schema creation — attempting recovery")
            try:
                c2 = conn.cursor()
                c2.execute("""CREATE TABLE IF NOT EXISTS users (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT UNIQUE NOT NULL,
                    email         TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_guest      INTEGER DEFAULT 0,
                    uuid          TEXT UNIQUE
                )""")
                conn.commit()
                c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'")
                if not c.fetchone():
                    raise Exception("'users' table still missing after recovery CREATE")
            except Exception:
                log.exception("init_db: unrecoverable — 'users' table could not be created")


        conn.commit()
    finally:
        conn.close()




def _hash(pw: str, salt: Optional[str] = None) -> str:
    """Hash a password with PBKDF2-HMAC-SHA256, 260 000 iterations, random salt."""
    if salt is None:
        salt = uuid.uuid4().hex
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 260_000)
    return f"{salt}${dk.hex()}"




def log_activity(
    user_id: int,
    action_type: str,
    detail: str = "",
    session_id=None,
) -> None:
    """Append an event to the audit log. Never raises — silently no-ops on error"""
    try:
        with _db() as conn:
            _execute(
                conn,
                "INSERT INTO user_activity "
                "(user_id, session_id, action_type, action_detail) "
                "VALUES (?,?,?,?)",
                (user_id, session_id, action_type, str(detail)[:1000]),
            )
    except Exception as exc:
        logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
        pass




def get_or_create_guest_user() -> dict:
    """Return the permanent local guest user row, creating it if needed."""
    import sqlite3
    conn = _connect()
    try:
        try:
            uid = _guest_row_id(conn)
        except sqlite3.OperationalError:
            conn.close()
            init_db()
            conn = _connect()
            try:
                uid = _guest_row_id(conn)
            except sqlite3.OperationalError:
                uid = None

        if uid:
            c = conn.cursor()
            c.execute(_ph("SELECT id, username FROM users WHERE id=?"), (uid,))
            row = c.fetchone()
            if row:
                # Migrate legacy rows created before we started storing the
                # real OS username (older installs may still have the
                # placeholder "__lytrize_guest__" value on disk).
                if row[1] == _GUEST_USERNAME:
                    new_name = _os_username()
                    try:
                        c.execute(_ph("UPDATE users SET username=? WHERE id=?"), (new_name, uid))
                        conn.commit()
                        return {"id": row[0], "username": new_name, "is_guest": True}
                    except Exception as exc:
                        logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
                return {"id": row[0], "username": row[1], "is_guest": True}

        c    = conn.cursor()
        cols = {"username": _os_username(), "email": _GUEST_EMAIL,
                "password_hash": _hash(uuid.uuid4().hex)}
        if _column_exists(conn, "users", "is_guest"):
            cols["is_guest"] = True
        if _column_exists(conn, "users", "uuid"):
            cols["uuid"] = uuid.uuid4().hex

        keys = ", ".join(cols.keys())
        phs  = ", ".join(["?"] * len(cols))
        try:
            c.execute(_ph(f"INSERT INTO users ({keys}) VALUES ({phs})"), tuple(cols.values()))
            conn.commit()
        except Exception:
            conn.rollback()

        uid = _guest_row_id(conn)
        if uid:
            c.execute(_ph("SELECT id, username FROM users WHERE id=?"), (uid,))
            row = c.fetchone()
            if row:
                return {"id": row[0], "username": row[1], "is_guest": True}
    finally:
        conn.close()

    # Final fallback: try a fresh connection
    try:
        with _db() as conn:
            uid2 = _guest_row_id(conn)
            if uid2:
                return {"id": uid2, "username": _GUEST_USERNAME, "is_guest": True}
    except Exception as exc:
        logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
        pass
    log.error("get_or_create_guest_user: failed to obtain a valid guest user_id")
    return {"id": None, "username": _GUEST_USERNAME, "is_guest": True}




def merge_user_data(source_user_id: int, target_user_id: int) -> None:
    """Reassign local data from a guest account to a newly signed-in account."""
    if not source_user_id or not target_user_id or source_user_id == target_user_id:
        return
    try:
        with _db() as conn:
            cur = conn.cursor()
            for table in ("sessions", "user_activity"):
                cur.execute(
                    f"UPDATE {table} SET user_id=? WHERE user_id=?",
                    (target_user_id, source_user_id),
                )


            cur.execute("SAVEPOINT draft_merge")
            try:
                cur.execute(
                    "DELETE FROM draft_sessions WHERE user_id=?",
                    (target_user_id,),
                )
                cur.execute(
                    "UPDATE draft_sessions SET user_id=? WHERE user_id=?",
                    (target_user_id, source_user_id),
                )
                cur.execute("RELEASE SAVEPOINT draft_merge")
            except Exception:
                cur.execute("ROLLBACK TO SAVEPOINT draft_merge")
                raise


        try:
            get_user_sessions.clear()
            get_user_stats.clear()
        except Exception as exc:
            logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
            pass
    except Exception as e:
        log.warning("merge_user_data: %s", e)




def save_draft(
    user_id: int,
    page: str,
    charts_json: str,
    file_name: str = "",
    editing_session_id=None,
    editing_session_name=None,
    editing_file_name: str = "",
    dashboard_title: str = "",
    kpis_json: str = "[]",
    chart_meta_json: str = "{}",
    layout_mode: str = "portrait",
) -> None:
    """Upsert the user's current in-progress state to draft_sessions."""
    try:
        with _db() as conn:
            _execute(conn, """
                    INSERT OR REPLACE INTO draft_sessions
                        (user_id, page, charts_json, file_name, editing_session_id,
                         editing_session_name, editing_file_name, dashboard_title,
                         kpis_json, chart_meta_json, layout_mode, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)""",
                    (user_id, page, charts_json, file_name,
                     editing_session_id, editing_session_name, editing_file_name,
                     dashboard_title, kpis_json, chart_meta_json, layout_mode),
                )
    except Exception as exc:
        logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
        pass




def get_draft(user_id: int) -> Optional[dict]:
    """Retrieve the stored draft for a user."""
    try:
        with _db() as conn:
            c = conn.cursor()
            c.execute(_ph("SELECT * FROM draft_sessions WHERE user_id=?"), (user_id,))
            row  = c.fetchone()
            desc = c.description
            if row and desc:
                return {col[0]: val for col, val in zip(desc, row)}
    except Exception as exc:
        logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
        pass
    return None




def clear_draft(user_id: int) -> None:
    """Delete the draft row after a successful session save."""
    try:
        with _db() as conn:
            _execute(conn, _ph("DELETE FROM draft_sessions WHERE user_id=?"), (user_id,))
    except Exception as exc:
        logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
        pass




def save_session_db(
    user_id: int,
    session_name: str,
    file_name: str,
    rows: int,
    cols: int,
    analysis_types: list,
    charts_json: str,
    dashboard_title: str = "",
    kpis_json: str = "[]",
    layout_mode: str = "portrait",
    grid_cols_n: int = 2,
    grid_order_json: str = "[]",
    grid_fullwidth_json: str = "{}",
    export_text_json: str = "{}",
    export_colours_json: str = "{}",
    session_uuid: Optional[str] = None,
    source: str = "local",
) -> int:
    """Insert a new saved session and return its DB row ID."""
    session_uuid = session_uuid or uuid.uuid4().hex
    with _db() as conn:
        c = conn.cursor()
        c.execute(
            _ph("""INSERT INTO sessions
               (user_id, session_uuid, session_name, file_name, rows_count, cols_count,
                analysis_types, charts_json, dashboard_title, kpis_json, layout_mode,
                grid_cols_n, grid_order_json, grid_fullwidth_json, export_text_json, export_colours_json, source, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)"""),
            (user_id, session_uuid, session_name, file_name, rows, cols,
             json.dumps(analysis_types), charts_json,
             dashboard_title, kpis_json, layout_mode,
             grid_cols_n, grid_order_json, grid_fullwidth_json, export_text_json, export_colours_json, source),
        )
        sid = _last_id(c)
    log_activity(user_id, "dashboard_saved",
                 f"session='{session_name}' file='{file_name}'", sid)
    get_user_sessions.clear()
    get_user_stats.clear()
    return sid




def get_session_uuid(session_id: int, user_id=None) -> Optional[str]:
    """Return the logical UUID for a session row."""
    try:
        with _db() as conn:
            c = conn.cursor()
            if user_id is None:
                c.execute(_ph("SELECT session_uuid FROM sessions WHERE id=?"), (session_id,))
            else:
                c.execute(
                    _ph("SELECT session_uuid FROM sessions WHERE id=? AND user_id=?"),
                    (session_id, user_id),
                )
            row = c.fetchone()
            return row[0] if row and row[0] else None
    except Exception:
        return None




def rename_session_db(session_id: int, new_name: str, user_id=None) -> None:
    """Rename a saved session. user_id guard prevents cross-account renames."""
    with _db() as conn:
        if user_id is None:
            _execute(conn,
                     _ph("UPDATE sessions SET session_name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?"),
                     (new_name, session_id))
        else:
            _execute(conn,
                     _ph("UPDATE sessions SET session_name=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?"),
                     (new_name, session_id, user_id))
    get_user_sessions.clear()
    get_user_stats.clear()




def sanitize_restored_session(
    session_data: dict,
    current_user_id: int,
    *,
    preserve_uuid: bool = True,
) -> dict:
    """Normalize imported backup sessions before inserting into the local DB."""


    cleaned = dict(session_data or {})


    cleaned["user_id"] = current_user_id


    if not preserve_uuid or not cleaned.get("session_uuid"):
        cleaned["session_uuid"] = str(uuid.uuid4())


    for key in (
        "device_id",
        "deleted_at",
        "is_deleted",
        "remote_id",
        "remote_uuid",
    ):
        cleaned.pop(key, None)


    return cleaned




def delete_session_db(session_id: int, user_id: int) -> bool:
    """Delete a saved session and clear cached session listings."""
    deleted = False
    with _db() as conn:
        cur = conn.cursor()
        cur.execute(_ph("DELETE FROM sessions WHERE id=? AND user_id=?"),
                    (session_id, user_id))
        deleted = getattr(cur, "rowcount", 0) > 0


        if not deleted:
            sess_uuid = None
            try:
                row = _execute_fetchone(
                    conn,
                    _ph("SELECT session_uuid FROM sessions WHERE id=?"),
                    (session_id,),
                )
                if row and row[0]:
                    sess_uuid = row[0]
            except Exception:
                sess_uuid = None


            if sess_uuid:
                cur.execute(_ph("DELETE FROM sessions WHERE session_uuid=? AND user_id=?"),
                            (sess_uuid, user_id))
                deleted = getattr(cur, "rowcount", 0) > 0


            if not deleted:
                cur.execute(_ph("DELETE FROM sessions WHERE id=?"), (session_id,))
                deleted = getattr(cur, "rowcount", 0) > 0




    try:
        get_user_sessions.clear()
        get_user_stats.clear()
    except Exception as exc:
        logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
        pass




    if not deleted:
        log.warning(
            "delete_session_db: no row deleted for session_id=%s user_id=%s",
            session_id,
            user_id,
        )
    return deleted




def update_session_db(
    session_id: int,
    session_name: str,
    charts_json: str,
    analysis_types: list,
    user_id: int,
    dashboard_title: str = "",
    kpis_json: str = "[]",
    layout_mode: str = "portrait",
    grid_cols_n: int = 2,
    grid_order_json: str = "[]",
    grid_fullwidth_json: str = "{}",
    export_text_json: str = "{}",
    export_colours_json: str = "{}",
    session_uuid: Optional[str] = None,
) -> None:
    """Overwrite a saved session in-place. user_id guard enforces ownership."""
    with _db() as conn:
        if session_uuid is not None:
            _execute(conn, _ph(
                "UPDATE sessions SET session_uuid=? "
                "WHERE id=? AND user_id=? AND (session_uuid IS NULL OR session_uuid='')"),
                (session_uuid, session_id, user_id),
            )
        _execute(conn, _ph(
            "UPDATE sessions "
            "SET session_name=?, charts_json=?, analysis_types=?, "
            "    dashboard_title=?, kpis_json=?, layout_mode=?, grid_cols_n=?, "
            "    grid_order_json=?, grid_fullwidth_json=?, export_text_json=?, export_colours_json=?, "
            "    updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND user_id=?"),
            (session_name, charts_json, json.dumps(analysis_types),
             dashboard_title, kpis_json, layout_mode, grid_cols_n,
             grid_order_json, grid_fullwidth_json, export_text_json, export_colours_json,
             session_id, user_id),
        )
    log_activity(user_id, "session_updated",
                 f"session_id={session_id} name='{session_name}'")
    get_user_sessions.clear()
    get_user_stats.clear()




@_ttl_cache(ttl_seconds=30)
def get_user_stats(user_id: int) -> dict:
    """Return summary counts for the Home page stat cards.

    - sessions: true total (not capped at 20 like get_user_sessions)
    - datasets: distinct source filenames across all sessions
    - analyses: total chart count across all sessions, derived from
      charts_json's array length rather than get_session_charts() (which
      fully deserializes every Plotly figure via pio.from_json -- far
      more expensive than needed just to count them).
    """
    if user_id is None:
        return {"sessions": 0, "datasets": 0, "analyses": 0}

    conn = _connect()
    try:
        c = conn.cursor()
        c.execute(_ph("SELECT COUNT(*) FROM sessions WHERE user_id=?"), (user_id,))
        session_count = c.fetchone()[0] or 0

        c.execute(
            _ph("SELECT COUNT(DISTINCT file_name) FROM sessions "
                "WHERE user_id=? AND file_name IS NOT NULL AND file_name != ''"),
            (user_id,),
        )
        dataset_count = c.fetchone()[0] or 0

        c.execute(_ph("SELECT charts_json FROM sessions WHERE user_id=?"), (user_id,))
        analysis_count = 0
        for (charts_json,) in c.fetchall():
            if not charts_json:
                continue
            try:
                analysis_count += len(json.loads(charts_json))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue

        return {"sessions": session_count, "datasets": dataset_count, "analyses": analysis_count}
    except Exception as e:
        log.warning("get_user_stats: %s", e)
        return {"sessions": 0, "datasets": 0, "analyses": 0}
    finally:
        conn.close()




@_ttl_cache(ttl_seconds=30)
def get_user_sessions(user_id: int) -> list:
    """Return the 20 most recent sessions for a user, newest first."""

    if user_id is None:
        return []
    conn = _connect()
    wanted = ["id", "session_name", "file_name", "rows_count",
              "cols_count", "analysis_types", "created_at"]
    try:
        c = conn.cursor()
        c.execute("PRAGMA table_info(sessions)")
        available_cols = {row[1] for row in c.fetchall()}


        select_cols = ", ".join(c for c in wanted if c in available_cols)
        if not select_cols:
            return []
        order_expr = (
            "COALESCE(updated_at, created_at)"
            if "updated_at" in available_cols
            else "created_at"
        )
        c.execute(
            _ph(f"SELECT {select_cols} FROM sessions "
                f"WHERE user_id=? ORDER BY {order_expr} DESC LIMIT 20"),
            (user_id,),
        )
        rows = c.fetchall()
        n_got = len(select_cols.split(","))
        n_wanted = len(wanted)
        if n_got < n_wanted:
            pad_count = n_wanted - n_got
            rows = [tuple(list(r) + [None] * pad_count) for r in rows]
        return rows
    except Exception as e:
        log.warning("get_user_sessions: %s", e)
        return []
    finally:
        conn.close()




def get_session_meta(session_id: int, user_id=None) -> Optional[dict]:
    """Fetch dashboard metadata (title, KPIs, layout) for a session."""
    try:
        with _db() as conn:
            c = conn.cursor()
            if user_id is None:
                c.execute(
                    _ph("SELECT dashboard_title, kpis_json, layout_mode, grid_cols_n, grid_order_json, grid_fullwidth_json, export_text_json, export_colours_json FROM sessions WHERE id=?"),
                    (session_id,),
                )
            else:
                c.execute(
                    _ph("SELECT dashboard_title, kpis_json, layout_mode, grid_cols_n, grid_order_json, grid_fullwidth_json, export_text_json, export_colours_json "
                        "FROM sessions WHERE id=? AND user_id=?"),
                    (session_id, user_id),
                )
            row = c.fetchone()
            if row:
                return {
                    "dashboard_title":    row[0] or "",
                    "kpis_json":          row[1] or "[]",
                    "layout_mode":        row[2] or "portrait",
                    "grid_cols_n":        row[3] if row[3] is not None else 2,
                    "grid_order_json":    row[4] or "[]",
                    "grid_fullwidth_json": row[5] or "{}",
                    "export_text_json":   row[6] or "{}",
                    "export_colours_json": row[7] or "{}",
                }
    except Exception as exc:
        logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
        pass
    return None




def get_session_charts(session_id: int, user_id=None) -> list:
    """Load and deserialise charts from a saved session."""
    import plotly.io as pio


    conn = _connect()
    try:
        c = conn.cursor()
        if user_id is None:
            c.execute(_ph("SELECT charts_json FROM sessions WHERE id=?"), (session_id,))
        else:
            c.execute(
                _ph("SELECT charts_json FROM sessions WHERE id=? AND user_id=?"),
                (session_id, user_id),
            )
        row = c.fetchone()
    finally:
        conn.close()


    if not (row and row[0]):
        return []


    try:
        raw_items = json.loads(row[0])
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        log.warning("get_session_charts: failed to parse charts_json for session %s: %s",
                    session_id, exc)
        return []


    charts = []
    for item in raw_items:
        try:
            uid           = item.get("uid", uuid.uuid4().hex[:8])
            desc          = item.get("desc", "")
            chart_type    = item.get("chart_type", "")
            meta          = item.get("meta", {})
            fig           = pio.from_json(item["fig_json"])
            charts.append((uid, item["title"], fig, desc, chart_type, meta))
        except Exception as exc:
            logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
            pass
    return charts




def delete_user_db(user_id: int) -> bool:
    """Permanently delete a user account and all associated data."""
    try:
        with _db() as conn:

            _execute(conn, _ph("DELETE FROM sessions        WHERE user_id=?"), (user_id,))
            _execute(conn, _ph("DELETE FROM users           WHERE id=?"),      (user_id,))
        return True
    except Exception as e:
        log.error("delete_user_db: %s", e)
        return False




def export_sessions_to_dict(
    user_id: int,
    username: str = "",
    local_db_path: str = "",
) -> list[dict]:
    """Export all sessions for user_id as a list of dicts for JSON backup."""
    local_sessions: list[dict] = []
    try:
        with _db() as conn:
            c = conn.cursor()
            c.execute("PRAGMA table_info(sessions)")
            available = {row[1] for row in c.fetchall()}
            wanted = [
                "session_uuid", "session_name", "file_name", "rows_count",
                "cols_count", "analysis_types", "charts_json", "dashboard_title",
                "kpis_json", "layout_mode", "grid_cols_n", "grid_order_json", "grid_fullwidth_json",
                "export_text_json", "export_colours_json", "source", "created_at",
            ]
            select_parts = [col for col in wanted if col in available]
            if "updated_at" in available:
                select_parts.append("COALESCE(updated_at, created_at) AS updated_at")
            elif "created_at" in available:
                select_parts.append("created_at AS updated_at")
            select_sql = ", ".join(select_parts)
            c.execute(
                f"SELECT {select_sql} FROM sessions WHERE user_id=? ORDER BY created_at",
                (user_id,),
            )
            cols = [d[0] for d in c.description]
            local_sessions = [dict(zip(cols, row)) for row in c.fetchall()]
    except Exception as e:
        log.warning("export_sessions_to_dict (local): %s", e)


    return local_sessions




def import_sessions_from_dict(
    user_id: int,
    sessions: list[dict],
) -> tuple[int, int, list[str]]:
    """Import sessions from a backup dict list into the local SQLite DB."""
    imported = 0
    updated  = 0
    skipped: list[str] = []


    def _parse_ts(value) -> Optional[datetime.datetime]:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            try:
                return datetime.datetime.fromtimestamp(value)
            except Exception:
                return None
        try:
            s = str(value).strip()
            if not s:
                return None
            s = s.replace("Z", "+00:00")
            return datetime.datetime.fromisoformat(s)
        except Exception:
            try:
                return datetime.datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None


    def _ts_for_compare(row: dict) -> datetime.datetime:
        return (
            _parse_ts(row.get("updated_at"))
            or _parse_ts(row.get("created_at"))
            or datetime.datetime.min
        )


    def _normalize_payload(raw: dict) -> dict:
        raw = {k: v for k, v in raw.items() if k != "_origin"}
        return sanitize_restored_session(raw, user_id, preserve_uuid=True)


    def _insert_row(conn, payload: dict) -> None:
        """Insert one session row. Defined once outside the loop for efficiency."""
        _execute(
            conn,
            _ph("""INSERT INTO sessions
                   (user_id, session_uuid, session_name, file_name, rows_count,
                    cols_count, analysis_types, charts_json, dashboard_title,
                    kpis_json, layout_mode, grid_cols_n, grid_order_json, grid_fullwidth_json,
                    export_text_json, export_colours_json, source, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)"""),
            (
                user_id,
                payload.get("session_uuid") or None,
                payload.get("session_name", "Restored Session"),
                payload.get("file_name"),
                payload.get("rows_count"),
                payload.get("cols_count"),
                payload.get("analysis_types"),
                payload.get("charts_json"),
                payload.get("dashboard_title", ""),
                payload.get("kpis_json", "[]"),
                payload.get("layout_mode", "portrait"),
                payload.get("grid_cols_n", 2) or 2,
                payload.get("grid_order_json", "[]"),
                payload.get("grid_fullwidth_json", "{}"),
                payload.get("export_text_json") or "{}",
                payload.get("export_colours_json") or "{}",
                payload.get("source") or "local",
                payload.get("created_at"),
            ),
        )


    try:
        with _db() as conn:
            c = conn.cursor()
            for raw in sessions:
                if not isinstance(raw, dict):
                    skipped.append("invalid-session")
                    continue


                s = _normalize_payload(raw)
                sess_uuid = s.get("session_uuid") or ""
                sname     = s.get("session_name", "Restored Session")
                source    = s.get("source") or "local"


                existing = None
                if sess_uuid:
                    c.execute(
                        _ph("SELECT id, COALESCE(updated_at, created_at) "
                            "FROM sessions WHERE session_uuid=? AND user_id=?"),
                        (sess_uuid, user_id),
                    )
                    existing = c.fetchone()


                if existing:
                    local_id      = existing[0]
                    local_updated  = _ts_for_compare({"updated_at": existing[1]})
                    backup_updated = _ts_for_compare(s)


                    if backup_updated and backup_updated > local_updated:
                        try:
                            _execute(
                                conn,
                                _ph("""UPDATE sessions SET
                                       session_name=?, file_name=?, rows_count=?,
                                       cols_count=?, analysis_types=?, charts_json=?,
                                       dashboard_title=?, kpis_json=?, layout_mode=?,
                                       grid_cols_n=?, grid_order_json=?, grid_fullwidth_json=?,
                                       export_text_json=?, export_colours_json=?, source=?, updated_at=CURRENT_TIMESTAMP
                                       WHERE id=? AND user_id=?"""),
                                (sname, s.get("file_name"), s.get("rows_count"),
                                 s.get("cols_count"), s.get("analysis_types"),
                                 s.get("charts_json"), s.get("dashboard_title", ""),
                                 s.get("kpis_json", "[]"), s.get("layout_mode", "portrait"),
                                 s.get("grid_cols_n", 2) or 2,
                                 s.get("grid_order_json", "[]"), s.get("grid_fullwidth_json", "{}"),
                                 s.get("export_text_json") or "{}",
                                 s.get("export_colours_json") or "{}",
                                 source, local_id, user_id),
                            )
                            updated += 1
                        except Exception as exc:
                            log.warning("import_sessions_from_dict: update failed: %s", exc)
                            skipped.append(sname)
                    else:
                        skipped.append(sname)
                    continue


                try:
                    _insert_row(conn, s)
                    imported += 1
                except Exception as exc:
                    try:
                        s2 = dict(s)
                        s2["session_uuid"] = uuid.uuid4().hex
                        _insert_row(conn, s2)
                        imported += 1
                    except Exception as exc2:
                        log.warning(
                            "import_sessions_from_dict: insert failed for %s: %s / %s",
                            sname, exc, exc2
                        )
                        skipped.append(sname)


        try:
            get_user_sessions.clear()
            get_user_stats.clear()
        except Exception as exc:
            logging.getLogger(__name__).debug("Suppressed error: %s", exc, exc_info=True)
            pass


    except Exception as e:
        log.warning("import_sessions_from_dict: %s", e)


    return imported, updated, skipped
