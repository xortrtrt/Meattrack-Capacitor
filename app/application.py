from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import web
from app.audit_context import begin_audit_context, end_audit_context
from app.config import APP_ENV, AUTH_RATE_LIMIT_ENABLED, CSRF_PROTECTION_ENABLED
from app.database import DatabaseBusyError
from app.portals import owner as owner_portal
from app.portals import refunds as refunds_portal
from app.portals import product_returns as returns_portal
from app.portals import reseller as reseller_portal
from app.portals import shared as shared_portal
from app.portals import team_leader as team_leader_portal
from app.routers import auth, chatbot, live_chat, public, system
from app.security_controls import csrf_token, enforce_csrf
from app.web_sessions import DatabaseSessionMiddleware


BASE_DIR = Path(__file__).resolve().parent


def database_busy_response():
    return JSONResponse(
        {"detail": "The service is busy. Please try again shortly."},
        status_code=503,
        headers={"Retry-After": "2"},
    )


class CachedStaticFiles(StaticFiles):
    """Give versioned local dependencies a long-lived browser cache."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        normalized_path = path.lstrip("/\\").replace("\\", "/")
        if response.status_code == 200 and normalized_path.startswith(("fonts/", "vendor/")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def create_app() -> FastAPI:
    application = FastAPI(
        title="MEATTRACK",
        version="0.1.0",
        dependencies=[Depends(enforce_csrf)],
    )
    application.add_middleware(DatabaseSessionMiddleware)
    application.mount(
        "/static",
        CachedStaticFiles(directory=BASE_DIR / "static"),
        name="static",
    )
    web.configure_templates(csrf_token)

    @application.middleware("http")
    async def security_headers(request: Request, call_next):
        audit_tokens = begin_audit_context(request)
        try:
            try:
                response = await call_next(request)
            except DatabaseBusyError:
                # Session loading/saving runs outside route exception handlers.
                response = database_busy_response()
        finally:
            end_audit_context(audit_tokens)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data: blob:; font-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "connect-src 'self' https://*.ably.io wss://*.ably.io "
            "https://*.ably.net wss://*.ably.net "
            "https://*.ably-realtime.com wss://*.ably-realtime.com; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        )
        return response

    @application.exception_handler(404)
    async def not_found(request: Request, exc: HTTPException):
        """Render a branded page for browsers while keeping API errors machine-readable."""
        accept = request.headers.get("accept", "")
        if request.url.path.startswith("/api/") or (
            "application/json" in accept and "text/html" not in accept
        ):
            return JSONResponse(
                {"detail": getattr(exc, "detail", "Not Found")},
                status_code=404,
            )
        return web.templates.TemplateResponse(
            request,
            "404.html",
            {"request": request},
            status_code=404,
        )

    if APP_ENV != "production" and (
        not AUTH_RATE_LIMIT_ENABLED or not CSRF_PROTECTION_ENABLED
    ):
        logging.getLogger(__name__).warning(
            "Local development security controls: AUTH_RATE_LIMIT_ENABLED=%s, "
            "CSRF_PROTECTION_ENABLED=%s",
            AUTH_RATE_LIMIT_ENABLED,
            CSRF_PROTECTION_ENABLED,
        )

    shared_portal.PORTAL_SECTION_LOADERS.update(
        {
            **owner_portal.SECTION_LOADERS,
            **team_leader_portal.SECTION_LOADERS,
            **reseller_portal.SECTION_LOADERS,
            **refunds_portal.SECTION_LOADERS,
            **returns_portal.SECTION_LOADERS,
            **{
                (role_key, "notifications"): shared_portal.notifications_context
                for role_key in ("owner", "team-leader", "sales-supervisor", "reseller")
            },
        }
    )

    for route_module in (system, public, chatbot, live_chat, auth):
        application.include_router(route_module.router)
    for portal_router in (
        owner_portal.router,
        team_leader_portal.router,
        reseller_portal.router,
        refunds_portal.router,
        returns_portal.router,
        shared_portal.router,
    ):
        application.include_router(portal_router)
    return application

