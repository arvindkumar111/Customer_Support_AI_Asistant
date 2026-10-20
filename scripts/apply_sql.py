"""Run one SQL file against the project database (works on Windows, no psql needed).
Usage (from project root):  python -m scripts.apply_sql db/migrations/001_refund_audit.sql
Safe to run twice: the migration files use IF NOT EXISTS.
"""
import sys
from pathlib import Path

import psycopg

from app import config

path = Path(sys.argv[1])
with psycopg.connect(config.DATABASE_URL, autocommit=True) as conn:
    conn.execute(path.read_text(encoding="utf-8"))
print(f"applied {path}")