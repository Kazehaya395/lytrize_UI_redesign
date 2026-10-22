"""services/database_service.py -- Headless database service.

Re-exports the pure database functions from ``modules.database`` and
replaces the Streamlit-specific ``@st.cache_data`` caching on
``get_user_sessions`` with a plain ``functools.lru_cache`` that the
caller can invalidate explicitly.

Usage (from Qt / scripts / tests)::

    from services.database_service import (
        init_db, get_user_sessions, save_session_db, ...
    )
    init_db()
    sessions = get_user_sessions(42)
    invalidate_sessions_cache()
"""

from __future__ import annotations

import functools
import json
import logging
from typing import Optional

# The underlying module imports streamlit at module level (which is fine —
# streamlit remains installed during migration). We only re-expose the pure
# functions and replace the Streamlit cache decorator.
from modules import database as _db

log = logging.getLogger(__name__)


# -- Re-export all pure database functions ----------------------------------

init_db = _db.init_db
log_activity = _db.log_activity
get_or_create_guest_user = _db.get_or_create_guest_user
merge_user_data = _db.merge_user_data
save_draft = _db.save_draft
get_draft = _db.get_draft
clear_draft = _db.clear_draft
save_session_db = _db.save_session_db
get_session_uuid = _db.get_session_uuid
rename_session_db = _db.rename_session_db
sanitize_restored_session = _db.sanitize_restored_session
delete_session_db = _db.delete_session_db
update_session_db = _db.update_session_db
get_session_meta = _db.get_session_meta
get_session_charts = _db.get_session_charts
get_user_stats = _db.get_user_stats
count_datasets_analysed = _db.count_datasets_analysed
record_dataset_analysed = _db.record_dataset_analysed
delete_user_db = _db.delete_user_db
export_sessions_to_dict = _db.export_sessions_to_dict
import_sessions_from_dict = _db.import_sessions_from_dict

# Schema / constant helpers
DB_PATH = _db.DB_PATH


# -- Headless session listing (replaces @st.cache_data) ----------------------

# The original get_user_sessions uses @st.cache_data, which requires a running
# Streamlit runtime. We re-implement it here with a plain functools cache.
# Call sites should use this version and call invalidate_sessions_cache()
# after any write to the sessions table.

@functools.lru_cache(maxsize=128)
def get_user_sessions(user_id: int) -> list:
    """Return the 20 most recent sessions for a user, newest first.

    Cached with ``functools.lru_cache``. Call ``invalidate_sessions_cache()``
    after any write to the ``sessions`` table.
    """
    if user_id is None:
        return []

    conn = _db._connect()
    wanted = ["id", "session_name", "file_name", "rows_count",
              "cols_count", "analysis_types", "created_at",
              "dataset_uri", "dataset_hash", "slicers_json"]
    try:
        c = conn.cursor()
        c.execute("PRAGMA table_info(sessions)")
        available_cols = {row[1] for row in c.fetchall()}

        select_cols = ", ".join(col for col in wanted if col in available_cols)
        if not select_cols:
            return []
        order_expr = (
            "COALESCE(updated_at, created_at)"
            if "updated_at" in available_cols
            else "created_at"
        )
        c.execute(
            _db._ph(f"SELECT {select_cols} FROM sessions "
                    f"WHERE user_id=? ORDER BY {order_expr} DESC LIMIT 20"),
            (user_id,),
        )
        rows = c.fetchall()
        # Pad MISSING columns in position (not just appended at the end):
        # Home reads dataset_uri at index 7 and slicers_json at index 9,
        # so a row from an old DB without those columns must carry None
        # in exactly those slots, or every later field shifts.
        have = [col for col in wanted if col in available_cols]
        full = [None] * len(wanted)
        fixed = []
        for r in rows:
            for pos, val in zip([wanted.index(c) for c in have], r):
                full[pos] = val
            fixed.append(tuple(full))
            full = [None] * len(wanted)
        return fixed
    except Exception as e:
        log.warning("get_user_sessions: %s", e)
        return []
    finally:
        conn.close()


def invalidate_sessions_cache() -> None:
    """Clear the cached session listings."""
    get_user_sessions.cache_clear()


def invalidate_all_caches() -> None:
    """Clear all caches in this service layer."""
    get_user_sessions.cache_clear()