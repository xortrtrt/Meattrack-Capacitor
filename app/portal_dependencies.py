from __future__ import annotations

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from app import portal_config
from app.business_time import business_now
from app.repositories import accounts
from app.web import path_with_query, redirect_to


def session_account_id(request: Request) -> int | None:
    value = request.session.get("account_id")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def session_team_leader_role(request: Request) -> str | None:
    value = request.session.get("team_leader_role")
    return value if value in {"inventory", "sales", "sales-supervisor"} else None


def safe_portal_path(
    role_key: str,
    section: str,
    message: str = "",
    error: str = "",
    **extra_params: str,
) -> str:
    if role_key not in portal_config.roles:
        raise HTTPException(status_code=404)
    if role_key in {"team-leader", "sales-supervisor"}:
        allowed_sections = {
            item[0]
            for nav in portal_config.team_leader_nav_by_role.values()
            for item in nav
        }
    else:
        allowed_sections = {item[0] for item in portal_config.portal_nav[role_key]}
    if section not in allowed_sections:
        raise HTTPException(status_code=404)
    params = {key: value for key, value in extra_params.items() if value}
    if message:
        params["message"] = message
    if error:
        params["error"] = error
    return path_with_query(f"/portal/{role_key}/{section}", **params)


def require_team_leader_role(request: Request, required_role: str) -> RedirectResponse | None:
    current_role = session_team_leader_role(request)
    if current_role == required_role or (
        required_role == "sales" and current_role == "sales-supervisor"
    ):
        return None
    role_key = (
        request.session.get("role_key")
        if request.session.get("role_key") in {"team-leader", "sales-supervisor"}
        else "team-leader"
    )
    return redirect_to(
        safe_portal_path(
            role_key,
            "dashboard",
            error="Your team leader account cannot access that action.",
        )
    )


def require_portal_session(request: Request, role_key: str) -> RedirectResponse | None:
    expires_at = request.session.get("portal_expires_at")
    try:
        expired = int(expires_at) <= int(business_now().timestamp())
    except (TypeError, ValueError):
        expired = True
    account_id = session_account_id(request)
    if request.session.get("role_key") == role_key and not expired and account_id:
        account = accounts.active_session_account(account_id)
        if account and account["role_key"] == role_key:
            leader_role_matches = role_key not in {"team-leader", "sales-supervisor"} or (
                account.get("team_leader_role") == request.session.get("team_leader_role")
            )
            if leader_role_matches:
                request.session["profile_image_path"] = account.get("profile_image_path") or ""
                request.session["profile_image_position_x"] = account.get("profile_image_position_x", 50)
                request.session["profile_image_position_y"] = account.get("profile_image_position_y", 50)
                request.session["profile_image_scale"] = account.get("profile_image_scale", 1)
                return None
    if request.session.get("role_key"):
        request.session.clear()
    return redirect_to(path_with_query("/login", error="Please sign in to access that portal."))
