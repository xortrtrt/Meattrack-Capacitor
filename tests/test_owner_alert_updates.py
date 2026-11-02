from app.portals import shared


def test_owner_badges_group_all_unread_targets_and_child_pages(monkeypatch):
    monkeypatch.setattr(shared.data, "unread_notification_targets", lambda account: [
        {"target_url": "/portal/owner/alerts", "total": 12},
        {"target_url": "/portal/owner/batches?batch=3", "total": 2},
        {"target_url": "/portal/owner/orders?order=1", "total": 1},
        {"target_url": "https://outside.test/portal/owner/orders", "total": 9},
    ])
    pages, parents = shared.owner_update_badges(7)
    assert pages == {"alerts": 12, "batches": 2, "orders": 1}
    assert parents == {"inventory": 14, "orders": 1}


def test_owner_badges_empty(monkeypatch):
    monkeypatch.setattr(shared.data, "unread_notification_targets", lambda account: [])
    assert shared.owner_update_badges(7) == ({}, {})
