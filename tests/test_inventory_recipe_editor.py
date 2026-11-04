from datetime import timedelta

import pytest
from app import repositories
from app.business_time import business_today
from app.database import fetch_one
from app.repositories.inventory.products import update_product_recipe
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401


@pytest.mark.postgres
def test_recipe_save_conversion_rollback_and_future_production(lifecycle):  # noqa: F811
    d = lifecycle
    material = repositories.add_raw_inventory_item('Recipe test meat', 'meat', 'kg', '10', d['inventory'])['raw_material_id']
    args = (d['product'], [material], ['250'], ['g'], '200', 'g', d['inventory'])
    update_product_recipe(*args)
    assert fetch_one('SELECT quantity_required q FROM product_recipes WHERE product_item_id=%s', (d['product'],))['q'] == pytest.approx(.25)
    assert fetch_one('SELECT pack_size FROM inventory_items WHERE item_id=%s', (d['product'],))['pack_size'] == 200
    with pytest.raises(ValueError):
        update_product_recipe(d['product'], [material], ['1'], ['ml'], '999', 'g', d['inventory'])
    assert fetch_one('SELECT pack_size FROM inventory_items WHERE item_id=%s', (d['product'],))['pack_size'] == 200
    with pytest.raises(ValueError):
        update_product_recipe(d['product'], [material,material], ['1','1'], ['kg','kg'], '200','g',d['inventory'])
    with pytest.raises(ValueError):
        update_product_recipe(*args[:-1], d['owner'])
    repositories.produce_product(d['product'], 'EDIT-PRODUCTION', '2', business_today()+timedelta(days=30), d['inventory'])
    assert fetch_one('SELECT quantity_available q FROM inventory_items WHERE item_id=%s', (material,))['q'] == pytest.approx(9.5)
