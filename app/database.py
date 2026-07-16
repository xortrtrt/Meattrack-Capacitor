import psycopg2
from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

from app.config import DATABASE_POOL_MAX, DATABASE_POOL_MIN, database_dsn

DSN = database_dsn()

# Initialize thread-safe connection pool
if DATABASE_POOL_MIN < 1 or DATABASE_POOL_MAX < DATABASE_POOL_MIN:
    raise ValueError("DATABASE_POOL_MIN/MAX define an invalid connection pool size")
pool = ThreadedConnectionPool(DATABASE_POOL_MIN, DATABASE_POOL_MAX, DSN)

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
