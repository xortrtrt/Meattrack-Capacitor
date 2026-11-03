from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app import portal_config
from app import repositories as data
from app import web
from app.business_time import business_now
from app.config import APP_ENV, LOGIN_OTP_ENABLED, PRIVACY_NOTICE_VERSION, TERMS_VERSION
from app.emailer import send_login_otp
from app.portal_dependencies import safe_portal_path
from app.security_controls import (
    enforce_otp_attempt,
    enforce_password_login,
    record_event,
    record_password_login,
)
from app.web_sessions import rotate_session


PORTAL_SESSION_SECONDS = 15 * 60
REMEMBER_ME_COOKIE_SECONDS = 30 * 24 * 60 * 60
LOGIN_DEVICE_COOKIE = "meattrack_device"
LOGIN_DEVICE_SECONDS = 30 * 24 * 60 * 60
router = APIRouter()
templates = web.templates
redirect_to = web.redirect_to
path_with_query = web.path_with_query
require_email = web.require_email


def establish_portal_session(request: Request, account: dict, *, remember_me: bool = False) -> RedirectResponse:
    role = account["role_key"]
    request.session.clear()
    request.session["account_id"] = account["account_id"]
    request.session["role_key"] = role
    request.session["account_name"] = account["name"]
    request.session["account_email"] = account["email"]
    request.session["profile_image_path"] = account.get("profile_image_path") or ""
    request.session["profile_image_position_x"] = account.get("profile_image_position_x", 50)
    request.session["profile_image_position_y"] = account.get("profile_image_position_y", 50)
    request.session["profile_image_scale"] = account.get("profile_image_scale", 1)
    request.session["portal_expires_at"] = int(business_now().timestamp()) + PORTAL_SESSION_SECONDS
    request.session["remember_me"] = remember_me
    if role in {"team-leader", "sales-supervisor"}:
        request.session["team_leader_role"] = account.get("team_leader_role") or "sales"
    rotate_session(request)
    data.add_log(account["name"], "login", portal_config.roles[role]["label"])
    return redirect_to(safe_portal_path(role, portal_config.default_section_for(role, account.get("team_leader_role"))))


def begin_login_otp(
    request: Request,
    account: dict,
    device_token: str,
    remember_me: bool = False,
) -> RedirectResponse:
    pending = data.request_login_otp(account["account_id"])
    sent, email_message = send_login_otp(
        to_email=account["email"],
        name=account["name"],
        otp_code=pending["otp_code"],
    )
    if not sent:
        data.cancel_login_otp(account["account_id"], pending.get("otp_id"))
        raise ValueError(email_message)
    request.session.clear()
    request.session["pending_login_account_id"] = account["account_id"]
    request.session["pending_login_email"] = account["email"]
    request.session["pending_login_device_token"] = device_token
    request.session["pending_login_remember_me"] = remember_me
    return redirect_to(path_with_query("/login", message="OTP sent to your account email. Enter it below to finish signing in."))


def _set_login_device_cookie(response: RedirectResponse, device_token: str) -> None:
    if not device_token:
        return
    response.set_cookie(
        LOGIN_DEVICE_COOKIE,
        device_token,
        max_age=LOGIN_DEVICE_SECONDS,
        httponly=True,
        secure=APP_ENV == "production",
        samesite="lax",
        path="/",
    )


def _complete_login(
    request: Request,
    account: dict,
    *,
    remember_me: bool,
    device_token: str = "",
):
    response = establish_portal_session(request, account, remember_me=remember_me)
    _set_login_device_cookie(response, device_token)
    return response


@router.get("/login")
async def login(request: Request, message: str = "", error: str = ""):
    try:
        account_id = int(request.session.get("account_id"))
        session_expiry = int(request.session.get("portal_expires_at", 0))
    except (TypeError, ValueError):
        account_id = 0
        session_expiry = 0
    session_role = request.session.get("role_key")
    account = (
        data.active_session_account(account_id)
        if account_id and session_expiry > int(business_now().timestamp())
        else None
    )
    if account and account.get("role_key") == session_role:
        leader_role_matches = session_role not in {"team-leader", "sales-supervisor"} or (
            account.get("team_leader_role") == request.session.get("team_leader_role")
        )
        if leader_role_matches:
            return redirect_to(
                safe_portal_path(
                    session_role,
                    portal_config.default_section_for(session_role, account.get("team_leader_role")),
                )
            )

    pending_login_account_id = request.session.get("pending_login_account_id")
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "roles": portal_config.roles,
            "message": message,
            "error": error,
            "privacy_notice_version": PRIVACY_NOTICE_VERSION,
            "terms_version": TERMS_VERSION,
            "login_otp_pending": LOGIN_OTP_ENABLED and bool(pending_login_account_id),
            "pending_login_email": request.session.get("pending_login_email", ""),
            "otp_resend_after_seconds": (
                data.login_otp_resend_after_seconds(int(pending_login_account_id))
                if LOGIN_OTP_ENABLED and pending_login_account_id
                else 0
            ),
        },
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/portal/session-activity", include_in_schema=False)
async def portal_session_activity():
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.get("/privacy")
async def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {"request": request, "policy_version": PRIVACY_NOTICE_VERSION})


@router.get("/terms")
async def terms(request: Request):
    return templates.TemplateResponse(request, "terms.html", {"request": request, "policy_version": TERMS_VERSION})


@router.get("/activate")
async def activation_page(request: Request, token: str = "", error: str = ""):
    activation_context = data.account_activation_context(token) if token else None
    return templates.TemplateResponse(
        request,
        "activate.html",
        {
            "request": request,
            "token": token,
            "error": error,
            "requires_policy_acceptance": bool(
                activation_context and activation_context.get("purpose") == "activation"
            ),
            "privacy_notice_version": PRIVACY_NOTICE_VERSION,
            "terms_version": TERMS_VERSION,
        },
    )


@router.post("/activate")
async def activation_submit(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    privacy_acknowledged: str = Form(""),
    terms_accepted: str = Form(""),
    privacy_notice_version: str = Form(""),
    terms_version: str = Form(""),
):
    try:
        if new_password != confirm_password:
            raise ValueError("Password and confirmation do not match.")
        activation_context = data.account_activation_context(token)
        if activation_context and activation_context.get("purpose") == "activation":
            if privacy_notice_version != PRIVACY_NOTICE_VERSION or terms_version != TERMS_VERSION:
                raise ValueError("The policy documents changed. Reload this page and review the current versions.")
        account = data.activate_account(
            token,
            new_password,
            privacy_notice_version=privacy_notice_version,
            terms_version=terms_version,
            privacy_acknowledged=privacy_acknowledged == "yes",
            terms_accepted=terms_accepted == "yes",
        )
    except ValueError as exc:
        activation_context = data.account_activation_context(token)
        return templates.TemplateResponse(
            request,
            "activate.html",
            {
                "request": request,
                "token": token,
                "error": str(exc),
                "requires_policy_acceptance": bool(
                    activation_context and activation_context.get("purpose") == "activation"
                ),
                "privacy_notice_version": PRIVACY_NOTICE_VERSION,
                "terms_version": TERMS_VERSION,
            },
            status_code=400,
        )
    rotate_session(request)
    message = (
        f"{account['name']} is activated. You can now sign in."
        if account.get("purpose") == "activation"
        else "Password updated. You can now sign in."
    )
    return redirect_to(path_with_query("/login", message=message))


@router.post("/login")
async def submit_login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    remember_me: str = Form(""),
):
    try:
        email = require_email(email)
        enforce_password_login(request, email)
        if len(password.strip()) < 4:
            raise ValueError("Password must be at least 4 characters.")
    except ValueError as exc:
        return redirect_to(path_with_query("/login", error=str(exc)))

    account = data.authenticate_account(email, password)
    if account is None:
        record_password_login(request, email, False)
        return redirect_to(path_with_query("/login", error="Invalid email or password."))
    record_password_login(request, email, True)
    should_remember = remember_me == "yes"

    if LOGIN_OTP_ENABLED:
        device_token = getattr(request, "cookies", {}).get(LOGIN_DEVICE_COOKIE, "")
        has_device_token = bool(re.fullmatch(r"[A-Za-z0-9_-]{32,128}", device_token))
        if not has_device_token:
            device_token = secrets.token_urlsafe(32)
        if has_device_token and data.login_device_is_trusted(account["account_id"], device_token):
            return _complete_login(
                request,
                account,
                remember_me=should_remember,
                device_token=device_token,
            )
        try:
            return begin_login_otp(request, account, device_token, should_remember)
        except ValueError as exc:
            return redirect_to(path_with_query("/login", error=str(exc)))

    return _complete_login(request, account, remember_me=should_remember)


@router.post("/login/otp")
async def submit_login_otp(request: Request, otp_code: str = Form(...)):
    if not LOGIN_OTP_ENABLED:
        return redirect_to(path_with_query("/login", error="Login OTP is disabled in this environment."))
    account_id = request.session.get("pending_login_account_id")
    if not account_id:
        return redirect_to(path_with_query("/login", error="Login OTP session expired. Please sign in again."))
    try:
        enforce_otp_attempt(int(account_id))
        account = data.confirm_login_otp(int(account_id), otp_code)
        device_token = request.session.get("pending_login_device_token")
        should_remember = bool(request.session.get("pending_login_remember_me"))
        if device_token:
            data.trust_login_device(int(account_id), device_token)
    except (TypeError, ValueError) as exc:
        record_event("otp_verify", f"account:{account_id}")
        return redirect_to(path_with_query("/login", error=str(exc)))
    record_event("otp_verify", f"account:{account_id}", succeeded=True)
    return _complete_login(
        request,
        account,
        remember_me=should_remember,
        device_token=device_token or "",
    )


@router.post("/login/otp/resend")
async def resend_login_otp(request: Request):
    if not LOGIN_OTP_ENABLED:
        return redirect_to(path_with_query("/login", error="Login OTP is disabled in this environment."))
    try:
        account_id = int(request.session.get("pending_login_account_id"))
    except (TypeError, ValueError):
        return redirect_to(path_with_query("/login", error="Login OTP session expired. Please sign in again."))
    device_token = request.session.get("pending_login_device_token") or ""
    remember_me = bool(request.session.get("pending_login_remember_me"))
    account = data.active_session_account(account_id)
    if not account:
        request.session.clear()
        return redirect_to(path_with_query("/login", error="Account was not found. Please sign in again."))
    try:
        return begin_login_otp(request, account, device_token, remember_me)
    except ValueError as exc:
        return redirect_to(path_with_query("/login", error=str(exc)))


@router.get("/account/policies")
async def account_policies_legacy():
    """Send old policy-prompt links back to sign-in; account policies are accepted during signup."""
    return redirect_to("/login")


@router.post("/account/policies")
async def accept_account_policies_legacy():
    return redirect_to("/login")


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    response = redirect_to(path_with_query("/login"))
    return response



