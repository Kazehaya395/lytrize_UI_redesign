"""backend.services -- Headless service layer for Lytrize.

This package provides pure-Python APIs that wrap the analytics, chart, database,
and session management logic.  Every function in this package is designed to
work WITHOUT Streamlit, so it can be called from scripts, tests, and the Qt UI
equally.

Migration rule
--------------
No ``import streamlit`` is allowed in this package.
All DataFrame and session state must be passed explicitly.
"""