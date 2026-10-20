import psycopg
from pgvector.psycopg import register_vector

from app import config


def get_conn() -> psycopg.Connection:
    """autocommit=True so we control transactions explicitly with `with conn.transaction():`."""
    conn = psycopg.connect(config.DATABASE_URL, autocommit=True)
    register_vector(conn)
    return conn
