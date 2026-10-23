from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

from fastapi import Request
from fastapi.testclient import TestClient
import httpx
import pytest

from app import database, security_controls, web_sessions
from app.application import create_app
from app.portals import product_returns, refunds


class Connection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    @contextmanager
    def cursor(self, **kwargs):
        yield self

    def execute(self, *args):
        pass

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class Pool:
    maxconn = 1

    def __init__(self):
        self.connection = Connection()
        self.borrowed = 0
        self.fail_get = False

    def getconn(self):
        if self.fail_get:
            raise RuntimeError("Connection setup failed")
        self.borrowed += 1
        assert self.borrowed == 1
        return self.connection

    def putconn(self, conn):
        assert conn is self.connection
        self.borrowed -= 1


@pytest.fixture
def one_connection_pool(monkeypatch):
    pool = Pool()
    monkeypatch.setattr(database, "pool", pool)
    monkeypatch.setattr(database, "POOL_ACQUIRE_TIMEOUT_SECONDS", .1)
    return pool


def test_cursor_helpers_share_capacity_and_wait_for_return(one_connection_pool):
    entered = Event()

    def waiting_request():
        entered.set()
        with database.get_db_cursor():
            return "finished"

    with ThreadPoolExecutor(max_workers=1) as executor:
        with database.get_transaction_cursor():
            future = executor.submit(waiting_request)
            assert entered.wait(1)
            assert not future.done()
        assert future.result(timeout=1) == "finished"
    assert one_connection_pool.borrowed == 0
    assert one_connection_pool.connection.commits == 2


def test_acquisition_timeout_does_not_leak_slots(one_connection_pool):
    with database.get_db_cursor():
        for helper in (database.get_db_cursor, database.get_transaction_cursor):
            with pytest.raises(database.DatabaseBusyError):
                with helper():
                    pytest.fail("Acquired an exhausted pool")
    with database.get_transaction_cursor():
        pass
    assert one_connection_pool.borrowed == 0


def test_rollback_and_failed_acquisition_release_capacity(one_connection_pool):
    with pytest.raises(ValueError):
        with database.get_transaction_cursor():
            raise ValueError("Abort this write")
    assert one_connection_pool.connection.rollbacks == 1
    one_connection_pool.fail_get = True
    with pytest.raises(RuntimeError, match="setup failed"):
        with database.get_db_cursor():
            pass
    one_connection_pool.fail_get = False
    with database.get_db_cursor():
        pass
    assert one_connection_pool.borrowed == 0


@pytest.mark.parametrize("stage", ["route", "session_load", "session_save"])
def test_busy_database_returns_503_even_during_session_io(monkeypatch, stage):
    def busy(*args):
        raise database.DatabaseBusyError("Internal connection details must not be exposed")

    if stage == "session_load":
        monkeypatch.setattr(web_sessions, "_load_session", busy)
    if stage == "session_save":
        monkeypatch.setattr(web_sessions, "_persist_session", busy)
    app = create_app()

    @app.get("/test-busy")
    async def endpoint(request: Request):
        if stage == "route":
            busy()
        request.session["changed"] = True
        return {"ok": True}

    with TestClient(app) as client:
        response = client.get("/test-busy")
    assert response.status_code == 503
    assert response.headers["retry-after"] == "2"
    assert response.json() == {"detail": "The service is busy. Please try again shortly."}
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("kind", ["refund", "return", "expired", "session"])
def test_slow_database_work_does_not_block_an_unrelated_request(monkeypatch, kind):
    entered, release = Event(), Event()

    def slow(*args, **kwargs):
        entered.set()
        if not release.wait(5):
            raise AssertionError("The event loop was blocked by database work")
        return {} if kind == "session" else None

    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", False)
    monkeypatch.setattr(refunds, "_allowed_role", lambda *args: (1, None))
    monkeypatch.setattr(product_returns, "_guard", lambda *args: None)
    monkeypatch.setattr(product_returns.shared, "session_account_id", lambda request: 1)
    path, data = "/portal/sales-supervisor/refunds/1/retry", {}
    if kind == "refund":
        monkeypatch.setattr(refunds.data, "start_order_refund", slow)
    elif kind == "return":
        monkeypatch.setattr(product_returns.returns, "reserve_replacement", slow)
        path, data = "/portal/team-leader/returns/actions/reserve", {"return_id": "1", "remarks": "Reserve replacement"}
    elif kind == "expired":
        monkeypatch.setattr(product_returns.returns, "confirm_expired_pullout", slow)
        path = "/portal/team-leader/expired-stock/actions/pullout"
        data = {"batch_id": "1", "quantity": "1", "location": "Freezer", "remarks": "Confirmed pullout", "operation_key": "unused"}
    else:
        monkeypatch.setattr(web_sessions, "_load_session", lambda token: slow() if token else {})
        monkeypatch.setattr(refunds.data, "start_order_refund", lambda *args: None)
    app = create_app()

    @app.get("/test-responsive")
    async def responsive():
        return {"ok": True}

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            headers = {"Cookie": "meattrack_session=test"} if kind == "session" else {}
            if kind == "session":
                from app.config import SESSION_COOKIE_NAME
                headers = {"Cookie": f"{SESSION_COOKIE_NAME}=test"}
            task = asyncio.create_task(client.post(path, data=data, headers=headers))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                response = await asyncio.wait_for(client.get("/test-responsive"), timeout=1)
                assert response.status_code == 200
                assert not task.done()
            finally:
                release.set()
            assert (await task).status_code == 303

    asyncio.run(exercise())
