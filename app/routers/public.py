from __future__ import annotations

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse

from app import repositories as data
from app import web


router = APIRouter()
templates = web.templates
redirect_to = web.redirect_to
path_with_query = web.path_with_query


def public_products(limit: int | None = None) -> list[dict]:
    try:
        if limit is not None:
            return data.list_products(active_only=True, page=1, page_size=limit)
        return data.list_products(active_only=True)
    except Exception:
        return []


def public_best_selling_products(limit: int = 3) -> list[dict]:
    try:
        return data.list_best_selling_products(limit=limit)
    except Exception:
        return public_products(limit=limit)


@router.get("/")
async def landing(request: Request, message: str = "", error: str = ""):
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "request": request,
            "message": message,
            "error": error,
            "products": public_products(limit=6),
        },
    )


@router.get("/products")
async def products(request: Request):
    return templates.TemplateResponse(
        request,
        "products.html",
        {
            "request": request,
            "products": public_products(),
        },
    )


@router.get("/about")
async def about(request: Request):
    return RedirectResponse("/#about", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/partnerships")
async def partnerships(request: Request):
    return RedirectResponse("/#partnerships", status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.post("/inquiries")
async def create_public_inquiry(
    name: str = Form(...),
    business_name: str = Form(...),
    email: str = Form(...),
    contact_number: str = Form(...),
    message: str = Form(...),
):
    return redirect_to(
        path_with_query(
            "/",
            error="Public sign-up is disabled. Please use the chatbot so a sales team leader can qualify your reseller inquiry.",
        )
        + "#inquiry"
    )



