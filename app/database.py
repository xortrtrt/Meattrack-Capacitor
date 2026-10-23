from decimal import Decimal

from psycopg2.pool import ThreadedConnectionPool
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from threading import BoundedSemaphore, Lock
from weakref import WeakKeyDictionary

from app.config import BUSINESS_TIMEZONE, DATABASE_POOL_MAX, DATABASE_POOL_MIN, database_dsn

DSN = database_dsn()

# Initialize thread-safe connection pool
if DATABASE_POOL_MIN < 1 or DATABASE_POOL_MAX < DATABASE_POOL_MIN:
    raise ValueError("DATABASE_POOL_MIN/MAX define an invalid connection pool size")

pool: ThreadedConnectionPool | None = None
_pool_lock = Lock()
_slots_lock = Lock()
_pool_slots: WeakKeyDictionary[ThreadedConnectionPool, BoundedSemaphore] = WeakKeyDictionary()
POOL_ACQUIRE_TIMEOUT_SECONDS = 10.0


class DatabaseBusyError(RuntimeError):
    """A request could not acquire a database connection within its wait budget."""


@contextmanager
def _pooled_connection():
    connection_pool = get_pool()
    with _slots_lock:
        slots = _pool_slots.get(connection_pool)
        if slots is None:
            slots = BoundedSemaphore(connection_pool.maxconn)
            _pool_slots[connection_pool] = slots
    if not slots.acquire(timeout=POOL_ACQUIRE_TIMEOUT_SECONDS):
        raise DatabaseBusyError("The service is busy. Please try again shortly.")
    try:
        conn = connection_pool.getconn()
        try:
            yield conn
        finally:
            connection_pool.putconn(conn)
    finally:
        slots.release()


def get_pool() -> ThreadedConnectionPool:
    """Create the connection pool on first database use, not during module import."""
    global pool
    if pool is None:
        with _pool_lock:
            if pool is None:
                pool = ThreadedConnectionPool(DATABASE_POOL_MIN, DATABASE_POOL_MAX, DSN)
    return pool

@contextmanager
def get_db_cursor():
    with get_transaction_cursor() as cur:
        yield cur


@contextmanager
def get_transaction_cursor():
    with _pooled_connection() as conn:
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT set_config('TimeZone', %s, false)", (BUSINESS_TIMEZONE,))
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise

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
