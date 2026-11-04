"""Public product catalog image delivery."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, Response

from app import repositories as data

router = APIRouter()


@router.get("/media/products/{product_id}")
def image_response(product_id: int):
    image = data.product_image(product_id)
    if not image:
        raise HTTPException(404)
    return Response(bytes(image["image_data"]), media_type=image["content_type"],
                    headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})


@router.get("/media/product-photo/{name}")
def named_image_response(name: str):
    from app.database import fetch_one
    from app.web import media_url, product_image

    image = fetch_one("""SELECT img.content_type,img.image_data FROM inventory_product_images img
        JOIN inventory_items i ON i.item_id=img.product_item_id WHERE lower(i.name)=lower(%s)
        AND i.item_type='finished_product' ORDER BY i.item_id LIMIT 1""", (name,))
    if image:
        return Response(bytes(image["image_data"]), media_type=image["content_type"],
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})
    return RedirectResponse(media_url(product_image(name)), headers={"Cache-Control": "no-cache"})
