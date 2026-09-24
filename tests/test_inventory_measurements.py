from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import inventory_measurements as measurements
from app import main, repositories
from tools.import_supabase_catalog import validate_and_normalize_catalog


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", Decimal("1.000")),
        ("1.2", Decimal("1.200")),
        ("1.250", Decimal("1.250")),
        ("0.001", Decimal("0.001")),
    ],
)
def test_exact_decimal_parser_accepts_supported_precision(raw, expected):
    assert measurements.parse_decimal_exact(raw, "Quantity", scale=3, positive=True) == expected


@pytest.mark.parametrize("raw", ["", "-1", "+1", "1e3", "NaN", "Infinity", "0.0004", "1.2501"])
def test_exact_decimal_parser_rejects_ambiguous_or_excess_precision(raw):
    with pytest.raises(ValueError):
        measurements.parse_decimal_exact(raw, "Quantity", scale=3, positive=True)


@pytest.mark.parametrize("raw", ["1.5", "0", "-1", "1e2", "1.000"])
def test_whole_pack_parser_rejects_noncanonical_pack_input(raw):
    with pytest.raises(ValueError, match="whole number|greater than zero"):
        measurements.parse_whole_packs(raw)


def test_whole_pack_parser_accepts_internal_integral_numeric_values():
    assert measurements.parse_whole_packs(Decimal("4.000")) == Decimal("4.000")
    assert measurements.parse_whole_packs(4.0) == Decimal("4.000")


def test_weight_and_volume_conversions_are_exact_and_family_safe():
    assert measurements.convert_quantity(Decimal("500.000"), "g", "kg", "Pork") == Decimal("0.500")
    assert measurements.convert_quantity(Decimal("1.000"), "kg", "g", "Pork") == Decimal("1000.000")
    with pytest.raises(ValueError, match="cannot be converted"):
        measurements.convert_quantity(Decimal("1.000"), "g", "ml", "Juice")
    with pytest.raises(ValueError, match="cannot be represented precisely"):
        measurements.convert_quantity(Decimal("0.500"), "g", "kg", "Spice")


def test_import_normalizes_legacy_mg_and_l_stock_with_dependent_recipe():
    today = date.today()
    items = [
        {
            "item_type": "raw_material", "name": "Spice", "category": "raw_material",
            "description": None, "unit": "mg", "base_price": Decimal("0"),
            "quantity_available": Decimal("5000"), "is_active": True, "created_at": today,
        },
        {
            "item_type": "raw_material", "name": "Juice", "category": "raw_material",
            "description": None, "unit": "L", "base_price": Decimal("0"),
            "quantity_available": Decimal("2"), "is_active": True, "created_at": today,
        },
        {
            "item_type": "finished_product", "name": "Test Pack", "category": "Pork",
            "description": "Test Pack - 500 g per pack.", "unit": "pack", "base_price": Decimal("10"),
            "quantity_available": Decimal("0"), "is_active": True, "created_at": today,
        },
    ]
    recipes = [{
        "product_type": "finished_product", "product_name": "Test Pack",
        "material_type": "raw_material", "material_name": "Spice",
        "quantity_required": Decimal("500"), "unit": "mg",
    }]
    batches = [{
        "item_type": "finished_product", "name": "Test Pack", "batch_code": "TEST-1",
        "source_type": "direct_received", "quantity_received": Decimal("5"),
        "quantity_available": Decimal("5"), "unit": "pack", "received_date": today,
        "expiry_date": today + timedelta(days=10), "quality_status": "approved", "created_at": today,
    }]

    normalized_items, normalized_recipes, normalized_batches = validate_and_normalize_catalog(items, recipes, batches)

    by_name = {item["name"]: item for item in normalized_items}
    assert by_name["Spice"]["unit"] == "g"
    assert by_name["Spice"]["quantity_available"] == Decimal("5.000")
    assert by_name["Juice"]["unit"] == "ml"
    assert by_name["Juice"]["quantity_available"] == Decimal("2000.000")
    assert normalized_recipes[0]["quantity_required"] == Decimal("0.500")
    assert normalized_recipes[0]["unit"] == "g"
    assert normalized_batches[0]["quantity_received"] == Decimal("5.000")


class _FefoCursor:
    def __init__(self, batches):
        self.batches = batches
        self._one = None
        self._all = []

    def execute(self, query, params=None):
        if "FROM inventory_items ii" in query:
            self._one = {"name": "Tocino", "unit": "pack"}
        elif "FROM inventory_batches ib" in query:
            self._all = self.batches

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


def test_fefo_planning_uses_earliest_batches_and_requires_full_stock():
    cursor = _FefoCursor([
        {"batch_id": 1, "quantity_available": Decimal("2"), "expiry_date": date.today()},
        {"batch_id": 2, "quantity_available": Decimal("5"), "expiry_date": date.today() + timedelta(days=1)},
    ])
    allocations = repositories._plan_fefo_allocations(cursor, {10: Decimal("6")})
    assert [(row["batch_id"], row["take"]) for row in allocations] == [
        (1, Decimal("2")),
        (2, Decimal("4")),
    ]

    with pytest.raises(ValueError, match="Required 8 packs, available 7 packs"):
        repositories._plan_fefo_allocations(cursor, {10: Decimal("8")})


def test_raw_inventory_route_preserves_original_decimal_text_and_actor(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "require_team_leader_role", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 42)
    monkeypatch.setattr(main.data, "add_raw_inventory_item", lambda *args, **kwargs: calls.append((args, kwargs)))

    response = TestClient(main.app).post(
        "/portal/team-leader/inventory-items",
        data={"name": "Pork", "category": "meat", "unit": "kg", "quantity": "1.250"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [(('Pork', 'meat', 'kg', '1.250'), {"actor_account_id": 42})]


def test_pack_content_route_passes_exact_values_and_actor(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "require_portal_session", lambda request, role: None)
    monkeypatch.setattr(main, "require_team_leader_role", lambda request, role: None)
    monkeypatch.setattr(main, "session_account_id", lambda request: 42)
    monkeypatch.setattr(main.data, "update_product_pack_content", lambda *args, **kwargs: calls.append((args, kwargs)))

    response = TestClient(main.app).post(
        "/portal/team-leader/products/7/pack-content",
        data={"pack_size": "500.000", "pack_size_unit": "g"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert calls == [((7, "500.000", "g"), {"actor_account_id": 42})]
