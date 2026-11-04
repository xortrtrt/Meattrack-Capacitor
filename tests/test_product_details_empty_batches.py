from datetime import timedelta
from uuid import uuid4
import pytest
from app import repositories
from app.business_time import business_today
from app.database import get_transaction_cursor
from app.repositories import product_returns
from app.repositories.inventory.manual_stock import remove_batch
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401


@pytest.mark.postgres
def test_hide_empty_batches_before_counting_and_pagination(lifecycle):  # noqa: F811
    d=lifecycle
    added=[repositories.add_product_batch(d['product'],f'HIDE-EMPTY-{i}','1',business_today()+timedelta(days=30),actor_account_id=d['inventory']) for i in range(4)]
    empty=added[0]['product_batch_id']
    remove_batch(d['product'],empty,'1','Empty batch visibility',str(uuid4()),d['inventory'],True)
    full=product_returns.product_lifecycle_details(d['product'])
    first=product_returns.product_lifecycle_details(d['product'],hide_empty_batches=True)
    last=product_returns.product_lifecycle_details(d['product'],hide_empty_batches=True,batch_page=9999)
    assert full['batch_pagination']['total']==6
    assert first['batch_pagination']['total']==5 and len(first['batches'])==3
    assert last['batch_pagination']['page']==2 and len(last['batches'])==2
    assert all(b['quantity_available']>0 and b['batch_id']!=empty for b in first['batches']+last['batches'])
    from starlette.requests import Request
    from app.portals.product_returns import product_details_context
    def context(role):
        request=Request({'type':'http','path':f'/portal/{role}/product-details','query_string':f'product_id={d["product"]}'.encode(),
                         'headers':[],'scheme':'http','server':('testserver',80)})
        return product_details_context(request)['lifecycle_product']
    assert context('team-leader')['batch_pagination']['total']==5
    assert context('owner')['batch_pagination']['total']==6
    assert first['warehouse']==full['warehouse']
    assert first['movement_pagination']==full['movement_pagination']
    with get_transaction_cursor() as cur:
        cur.execute('UPDATE inventory_batches SET quantity_available=0 WHERE item_id=%s',(d['product'],))
    cleared=product_returns.product_lifecycle_details(d['product'],hide_empty_batches=True,batch_page=9999)
    assert cleared['batches']==[] and cleared['batch_pagination']['total']==0
    assert cleared['movements']
