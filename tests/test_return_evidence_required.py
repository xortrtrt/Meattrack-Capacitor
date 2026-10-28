"""Supporting photos are required by the request endpoint and creation service."""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app import main
from app.database import fetch_one
from app.repositories import product_returns as returns
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle, supporting_photo  # noqa: F401
from test_return_request_options import sign_in

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("resolution", ["refund", "replace"])
def test_new_requests_reject_missing_empty_invalid_and_excess_photos(lifecycle, monkeypatch, resolution):  # noqa: F811
    d = lifecycle
    sign_in(monkeypatch, d["reseller"], "reseller")
    photo = supporting_photo()
    data = dict(order_id=d["order"], allocation_id=d["allocation"], quantity=2,
                resolution=resolution, remarks="Supporting photo of the issue for Sales review", operation_key=str(uuid4()))
    invalid_uploads = [
        None,
        {"evidence": ("empty.png", b"", "image/png")},
        {"evidence": ("broken.png", b"not an image", "image/png")},
        [("evidence", (str(i)+".png", photo["content"], "image/png")) for i in range(4)],
    ]
    with TestClient(main.app) as client:
        for files in invalid_uploads:
            response = client.post("/portal/reseller/returns/actions/submit", data=data, files=files)
            assert response.status_code == 200 and "returns-dialog-notice is-error" in response.text
            assert 'data-auto-open="true"' in response.text
            assert fetch_one("SELECT count(*) n FROM product_returns WHERE order_id=%s", (d["order"],))["n"] == 0
            assert fetch_one("SELECT count(*) n FROM return_attachments")["n"] == 0
            assert fetch_one("SELECT count(*) n FROM product_return_events")["n"] == 0
        response = client.post("/portal/reseller/returns/actions/submit", data=data,
            files={"evidence": (photo["filename"], photo["content"], photo["content_type"])})
        assert response.status_code == 200 and 'data-return-dialog=' in response.text
        case = fetch_one("SELECT return_id,resolution FROM product_returns WHERE order_id=%s", (d["order"],))
        assert case and case["resolution"] == resolution
        evidence = fetch_one("SELECT purpose,content,uploaded_by_account_id FROM return_attachments WHERE return_id=%s", (case["return_id"],))
        assert evidence["purpose"] == "evidence" and bytes(evidence["content"]) == photo["content"]
        assert evidence["uploaded_by_account_id"] == d["reseller"]


def test_creation_service_requires_photo_for_new_case_and_keeps_replays_idempotent(lifecycle):  # noqa: F811
    d = lifecycle
    args = (d["order"], d["reseller"], "refund", [(d["allocation"], 2)], "Required photo enforcement in the return service")
    with pytest.raises(ValueError, match="at least one supporting photo"):
        returns.create_product_return(*args)
    with pytest.raises(ValueError, match="up to 3 supporting photos"):
        returns.create_product_return(*args, attachments=[supporting_photo()]*4)
    assert fetch_one("SELECT count(*) n FROM product_returns")["n"] == 0
    key = str(uuid4())
    photos = [supporting_photo()]
    case = returns.create_product_return(*args, attachments=photos, operation_key=key)
    assert returns.create_product_return(*args, attachments=photos, operation_key=key) == case
    assert fetch_one("SELECT count(*) n FROM product_returns")["n"] == 1
    assert fetch_one("SELECT count(*) n FROM return_attachments WHERE return_id=%s", (case,))["n"] == 1
