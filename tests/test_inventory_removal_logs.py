from datetime import timedelta
from uuid import uuid4

import pytest
from app import repositories
from app.business_time import business_today
from app.repositories.inventory import manual_stock as stock
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401


@pytest.mark.postgres
def test_removal_logs_include_status_and_stock_changes_without_duplicates(lifecycle, monkeypatch):  # noqa: F811
    d=lifecycle
    material=repositories.add_raw_inventory_item('Removal log material','meat','kg','10',d['inventory'])['raw_material_id']
    stock.set_material_active(material,False,d['inventory'],True)
    stock.set_material_active(material,False,d['inventory'],True)
    rows=repositories.list_inventory_movements(q='Removal log material',category='removals')
    assert len(rows)==1 and rows[0]['action']=='removed_raw_material'
    assert rows[0]['quantity_delta']==0 and rows[0]['balance_after']==10
    assert rows[0]['actor_name']=='Inventory'
    stock.set_material_active(material,True,d['inventory'],True)
    assert repositories.count_inventory_movements(q='Removal log material',category='removals')==2
    stock.remove_batch(d['product'],d['batches'][1],'20','Damaged removal test',str(uuid4()),d['inventory'],True)
    rows=repositories.list_inventory_movements(q='Damaged removal test',category='removals')
    assert len(rows)==1 and rows[0]['action']=='batch_removed' and rows[0]['quantity_delta']==-20
    assert repositories.count_inventory_movements(q=rows[0]['batch_code'],category='removals')==1
    batch=repositories.add_product_batch(d['product'],'EMPTY-REMOVAL','2',business_today()+timedelta(days=30),actor_account_id=d['inventory'])
    stock.dispatch_stock(d['product'],'2','Empty the new batch',str(uuid4()),d['inventory'])
    stock.remove_batch(d['product'],batch['product_batch_id'],'0','Empty removal test',str(uuid4()),d['inventory'],True)
    rows=repositories.list_inventory_movements(q='EMPTY-REMOVAL',category='removals')
    assert len(rows)==1 and rows[0]['quantity_delta']==0 and rows[0]['action']=='batch_removed'
    all_rows=repositories.list_inventory_movements(category='removals',page=1,page_size=1)
    assert len(all_rows)==1
    assert repositories.count_inventory_movements(category='removals')==4

    from app import main, web_sessions
    from app.business_time import business_now
    from fastapi.testclient import TestClient
    session={'account_id':d['inventory'],'role_key':'team-leader','team_leader_role':'inventory',
             'portal_expires_at':int(business_now().timestamp())+600}
    monkeypatch.setattr(web_sessions,'_load_session',lambda token:dict(session))
    monkeypatch.setattr(web_sessions,'_persist_session',lambda token,values,*args:session.update(values))
    with TestClient(main.app) as client:
        response=client.get('/portal/team-leader/logs?type=removals')
        assert response.status_code==200
        for label in ['Batch removed','Raw material removed','Raw material restored','EMPTY-REMOVAL','Damaged removal test']:
            assert label in response.text
