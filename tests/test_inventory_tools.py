"""Product catalog photo storage and delivery."""
from io import BytesIO

import pytest
from PIL import Image
from fastapi.testclient import TestClient

from app import main, repositories
from app.database import fetch_one
from app.repositories.inventory import product_image, save_product_image
from app.uploads import validate_product_image
from test_inventory_postgres import strict_inventory_database  # noqa: F401
from test_product_lifecycle import lifecycle  # noqa: F401


def photo():
    stream = BytesIO()
    Image.new("RGB", (8, 8), "gold").save(stream, "PNG")
    return {"content_type": "image/png", "content": stream.getvalue()}


@pytest.mark.postgres
def test_product_photo_storage_catalog_delivery_and_atomic_recipe(lifecycle):
    data = lifecycle
    material = repositories.add_raw_inventory_item("Photo test meat", "meat", "kg", "10", data["inventory"])["raw_material_id"]
    image = photo()
    product = repositories.create_product_with_recipe(
        "Photo product", "Pork", "0", [material], ["1"], ["kg"], "500", "g",
        data["inventory"], product_photo=image,
    )
    assert bytes(product_image(product["product_id"])["image_data"]) == image["content"]
    save_product_image(data["product"], image, data["inventory"])
    with TestClient(main.app) as client:
        response = client.get("/media/product-photo/Test%20Product")
        assert response.status_code == 200 and response.content == image["content"]
        assert client.get("/media/product-photo/Unknown", follow_redirects=False).status_code == 307
    with pytest.raises(ValueError):
        repositories.create_product_with_recipe(
            "Broken photo", "Pork", "0", [material], ["1"], ["kg"], "500", "g",
            data["inventory"], product_photo={"content_type": "image/png", "content": b"broken"},
        )
    assert not fetch_one("SELECT 1 FROM inventory_items WHERE name='Broken photo'")


@pytest.mark.parametrize(
    "content_type,content",
    [("image/png", b"broken"), ("image/jpeg", photo()["content"]), ("image/png", b"x" * (2 * 1024 * 1024 + 1))],
    ids=["broken", "mime-mismatch", "oversize"],
)
def test_photo_validation(content_type, content):
    with pytest.raises(ValueError):
        validate_product_image(content_type, content)
