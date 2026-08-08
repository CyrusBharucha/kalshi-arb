"""dashboard/db.py — minimal psycopg2 connection helper for dashboard pages."""
import os
import psycopg2


def get_db_conn():
    """Return a live psycopg2 connection or None if DB is unavailable."""
    url = (
        os.environ.get("NEON_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or ""
    ).strip()
    if not url:
        try:
            import streamlit as st
            url = (st.secrets.get("DATABASE_URL") or "").strip()
        except Exception:
            pass
    if not url:
        return None
    try:
        return psycopg2.connect(url, connect_timeout=5)
    except Exception:
        return None
