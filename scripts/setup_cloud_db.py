"""
setup_cloud_db.py
=================
One-time setup script: create PostgreSQL schema and seed it from the bundled
SQLite snapshot (dashboard/dashboard.db).

Usage (run locally after setting DATABASE_URL):
    export DATABASE_URL="postgresql://user:pass@host/dbname"
    python setup_cloud_db.py

Or on Windows:
    set DATABASE_URL=postgresql://user:pass@host/dbname
    python setup_cloud_db.py

After this script succeeds, add the same DATABASE_URL to your Streamlit Cloud
secrets and redeploy — the dashboard will run in fully-live mode.
"""

import os
import sys
import sqlite3
from pathlib import Path

# --- Resolve paths -----------------------------------------------------------
ROOT = Path(__file__).parent
SQLITE_PATH = ROOT / "dashboard" / "dashboard.db"

sys.path.insert(0, str(ROOT))

# Pull DATABASE_URL from env (or prompt)
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
if not DATABASE_URL:
    DATABASE_URL = input("Enter your PostgreSQL DATABASE_URL: ").strip()
if not DATABASE_URL:
    print("ERROR: DATABASE_URL is required.")
    sys.exit(1)

# Normalise: Neon uses postgres:// scheme
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if "+" not in DATABASE_URL.split("://")[0]:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

print(f"Connecting to: {DATABASE_URL[:DATABASE_URL.index('@')]}@***")

# --- Create schema -----------------------------------------------------------
from sqlalchemy import create_engine, text

engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 15}, pool_pre_ping=True)

with engine.connect() as conn:
    conn.execute(text("SELECT 1"))
print("✓ Connected to PostgreSQL")

# Import models and create all tables
os.environ["DATABASE_URL"] = DATABASE_URL
import config  # noqa — populates DB_URL
from database.models import Base
Base.metadata.create_all(engine)
print("✓ Schema created (all tables)")

# --- Seed from SQLite snapshot -----------------------------------------------
if not SQLITE_PATH.exists():
    print("WARNING: dashboard/dashboard.db not found — skipping seed.")
    print("Schema is ready; connect and start the collectors to populate data.")
    sys.exit(0)

print(f"Seeding from {SQLITE_PATH} …")

sq = sqlite3.connect(str(SQLITE_PATH))
sq.row_factory = sqlite3.Row

TABLES = [
    "markets",
    "contract_relationships",
    "arbitrage_opportunities",
]

from sqlalchemy.dialects.postgresql import insert as pg_insert
import pandas as pd

for table in TABLES:
    try:
        df = pd.read_sql_query(f"SELECT * FROM {table}", sq)
        if df.empty:
            print(f"  {table}: empty — skipped")
            continue
        # Use pandas to_sql with if_exists='append'; conflict-safe via try/except
        df.to_sql(table, engine, if_exists="append", index=False, method="multi",
                  chunksize=500)
        print(f"  ✓ {table}: {len(df):,} rows inserted")
    except Exception as e:
        print(f"  ✗ {table}: {e}")

sq.close()
print("\n✓ Done. Add DATABASE_URL to Streamlit Cloud secrets and redeploy.")
