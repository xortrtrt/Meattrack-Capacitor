from unittest.mock import Mock

from fastapi.testclient import TestClient

from app import security_controls
from app.application import create_app
from app.portals import product_returns


def test_disposal_form_does_not_require_method(monkeypatch):
    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", False)
    monkeypatch.setattr(product_returns, "_guard", lambda *args: None)
    monkeypatch.setattr(product_returns.shared, "session_account_id", lambda request: 1)
    dispose = Mock()
    monkeypatch.setattr(product_returns.returns, "dispose_expired_stock", dispose)
    with TestClient(create_app()) as client:
        response = client.post(
            "/portal/team-leader/expired-stock/actions/dispose",
            data={"holding_id": "2", "quantity": "3", "remarks": "Disposed with witness", "operation_key": "test-key"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    dispose.assert_called_once_with(
        2, method=None, actor_id=1, quantity="3", remarks="Disposed with witness", operation_key="test-key"
    )


def test_expired_arrival_action_is_removed(monkeypatch):
    monkeypatch.setattr(security_controls, "CSRF_PROTECTION_ENABLED", False)
    monkeypatch.setattr(product_returns, "_guard", lambda *args: None)
    monkeypatch.setattr(product_returns.shared, "session_account_id", lambda request: 1)
    arrival = Mock()
    monkeypatch.setattr(product_returns.returns, "record_expired_arrival", arrival)
    with TestClient(create_app()) as client:
        response = client.post("/portal/team-leader/expired-stock/actions/arrival", follow_redirects=False)
    assert response.status_code == 404
    arrival.assert_not_called()
