"""
dashboard/neon_utils.py
=======================
Shared Neon PostgreSQL connectivity helpers.
Replaces the ~15-line boilerplate duplicated across 9+ dashboard pages.
"""
from __future__ import annotations

from typing import Optional

import streamlit as st


def is_neon_connected() -> bool:
    """Return True if Neon PostgreSQL is reachable."""
    if bool(st.session_state.get("_sidebar_neon_ok", False)):
        return True
    try:
        import dashboard.live_arb_store as _las
        if bool(getattr(_las, "_pg_ok", False)):
            return True
        if getattr(_las, "_pg_engine", None) is not None:
            return True
        if _las.get_pg_engine_cached() is not None:
            return True
    except Exception:
        pass
    return False


def get_neon_engine():
    """Return the Neon SQLAlchemy engine, or None."""
    try:
        import dashboard.live_arb_store as _las
        eng = getattr(_las, "_pg_engine", None) or _las.get_pg_engine_cached()
        return eng
    except Exception:
        return None
