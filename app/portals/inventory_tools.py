"""Inventory-only tools; all stock writes run in the worker thread pool."""
from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from app.capabilities import require_capability
from app.portals import shared
from app.repositories.inventory import tools
from app.uploads import parse_product_image

router = APIRouter()


def workspace_context(request: Request):
    if request.query_params.get('view', 'materials') not in {'materials', 'packages', 'removed'}:
        raise HTTPException(404)
    return {**tools.tools_workspace(), 'new_reference': lambda: str(uuid4()),
            'tools_view': request.query_params.get('view', 'materials')}


SECTION_LOADERS = {('team-leader', 'inventory-tools'): workspace_context}


def _guard(request):
    guard = shared.require_portal_session(request, 'team-leader')
    if guard:
        return guard
    require_capability(request, 'inventory.manage')
    return None


@router.post('/portal/team-leader/inventory-tools/{action}')
async def inventory_action(request: Request, action: str):
    guard = _guard(request)
    if guard:
        return guard
    form = await request.form()
    actor = shared.session_account_id(request)
    view = 'packages' if action.startswith('package-') else 'materials'
    target = 'inventory-tools'
    extra = {'view': view}
    try:
        if action == 'deduct':
            await run_in_threadpool(tools.adjust_raw_material, int(str(form.get('item_id', ''))),
                form.get('quantity', ''), form.get('reason', ''), form.get('reference', ''), actor)
        elif action in {'remove-material', 'reactivate-material'}:
            await run_in_threadpool(tools.set_material_active, int(str(form.get('item_id', ''))),
                action == 'reactivate-material', actor, form.get('confirmed') == 'yes')
        elif action == 'remove-batch':
            product_id = int(str(form.get('product_id', '')))
            target, extra = 'product-details', {'product_id': str(product_id)}
            await run_in_threadpool(tools.remove_batch, product_id, int(str(form.get('batch_id', ''))),
                form.get('expected_quantity', ''), form.get('reason', ''), form.get('reference', ''), actor,
                form.get('confirmed') == 'yes')
        elif action in {'package-save', 'package-dispatch'}:
            await run_in_threadpool(tools.package_action, int(str(form.get('package_id', ''))),
                str(form.get('mode', 'standard')), form.getlist('product_id[]'), form.getlist('packs[]'),
                form.get('quantity', ''), form.get('reason', ''), form.get('reference', ''), actor,
                save=action == 'package-save', confirmed=form.get('confirmed') == 'yes')
        elif action == 'photo':
            product_id = int(str(form.get('product_id', '')))
            target, extra = 'product-details', {'product_id': str(product_id)}
            upload = form.get('photo')
            image = await parse_product_image(upload if isinstance(upload, UploadFile) else None)
            if image is None:
                raise ValueError('Choose a product photo.')
            await run_in_threadpool(tools.save_product_image, product_id, image, actor)
        else:
            raise HTTPException(404)
    except (ValueError, TypeError) as exc:
        # A successful response reuses no submission references. Failed forms can safely retry.
        return shared.redirect_to(shared.safe_portal_path('team-leader', target, error=str(exc), **extra))
    return shared.redirect_to(shared.safe_portal_path('team-leader', target, message='Inventory change recorded.', **extra))


@router.get('/media/products/{product_id}')
def image_response(product_id: int):
    # Product catalog photos are public catalog media, not private return/payment evidence.
    image = tools.product_image(product_id)
    if not image:
        raise HTTPException(404)
    return Response(bytes(image['image_data']), media_type=image['content_type'],
                    headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})


@router.get('/media/product-photo/{name}')
def named_image_response(name: str):
    from app.database import fetch_one
    from app.web import product_image, media_url
    image = fetch_one("""SELECT img.content_type,img.image_data FROM inventory_product_images img
        JOIN inventory_items i ON i.item_id=img.product_item_id WHERE lower(i.name)=lower(%s)
        AND i.item_type='finished_product' ORDER BY i.item_id LIMIT 1""", (name,))
    if image:
        return Response(bytes(image['image_data']), media_type=image['content_type'],
                        headers={'Cache-Control': 'no-cache', 'X-Content-Type-Options': 'nosniff'})
    return RedirectResponse(media_url(product_image(name)), headers={'Cache-Control': 'no-cache'})
