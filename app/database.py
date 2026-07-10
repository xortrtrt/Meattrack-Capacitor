import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

from app.config import DATABASE_URL

DSN = DATABASE_URL

# Initialize thread-safe connection pool
pool = ThreadedConnectionPool(1, 10, DSN)

@contextmanager
def get_db_cursor():
    conn = pool.getconn()
    try:
        # RealDictCursor maps column names to dictionary keys
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
            conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)

@contextmanager
def get_transaction_cursor():
    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            yield cur
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        pool.putconn(conn)

def fetch_all(query, params=None):
    with get_db_cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()

def fetch_one(query, params=None):
    with get_db_cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()

def execute_write(query, params=None, returning=False):
    with get_db_cursor() as cur:
        cur.execute(query, params)
        if returning:
            return cur.fetchone()

from decimal import Decimal
def clean_row(row):
    if row is None:
        return None
    if isinstance(row, list):
        return [clean_row(r) for r in row]
    new_row = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            new_row[k] = float(v)
        else:
            new_row[k] = v
    return new_row
