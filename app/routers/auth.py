from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from app import portal_config
from app import repositories as data
from app import web
from app.business_time import business_now
from app.config import CONSENT_VERSION, LOGIN_OTP_ENABLED
from app.emailer import send_login_otp
from app.portal_dependencies import safe_portal_path
from app.security_controls import (
    enforce_otp_attempt,
    enforce_password_login,
    record_event,
    record_password_login,
)
from app.web_sessions import rotate_session


PORTAL_SESSION_SECONDS = 2 * 60 * 60
router = APIRouter()
templates = web.templates
redirect_to = web.redirect_to
path_with_query = web.path_with_query
require_email = web.require_email


def establish_portal_session(request: Request, account: dict) -> RedirectResponse:
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
    if role in {"team-leader", "sales-supervisor"}:
        request.session["team_leader_role"] = account.get("team_leader_role") or "sales"
    rotate_session(request)
    data.add_log(account["name"], "login", portal_config.roles[role]["label"])
    return redirect_to(safe_portal_path(role, portal_config.default_section_for(role, account.get("team_leader_role"))))


def begin_login_otp(request: Request, account: dict, consent_source: str = "password_otp") -> RedirectResponse:
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
    request.session["pending_login_consent_version"] = CONSENT_VERSION
    request.session["pending_login_consent_source"] = consent_source
    return redirect_to(path_with_query("/login", message="OTP sent to your account email. Enter it below to finish signing in."))


@router.get("/login")
async def login(request: Request, message: str = "", error: str = ""):
    pending_login_account_id = request.session.get("pending_login_account_id")
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "request": request,
            "roles": portal_config.roles,
            "message": message,
            "error": error,
            "consent_version": CONSENT_VERSION,
            "login_otp_pending": LOGIN_OTP_ENABLED and bool(pending_login_account_id),
            "pending_login_email": request.session.get("pending_login_email", ""),
        },
    )


@router.get("/privacy")
async def privacy(request: Request):
    return templates.TemplateResponse(request, "privacy.html", {"request": request, "consent_version": CONSENT_VERSION})


@router.get("/terms")
async def terms(request: Request):
    return templates.TemplateResponse(request, "terms.html", {"request": request, "consent_version": CONSENT_VERSION})


@router.get("/activate")
async def activation_page(request: Request, token: str = "", error: str = ""):
    return templates.TemplateResponse(
        request,
        "activate.html",
        {"request": request, "token": token, "error": error},
    )


@router.post("/activate")
async def activation_submit(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
):
    try:
        if new_password != confirm_password:
            raise ValueError("Password and confirmation do not match.")
        account = data.activate_account(token, new_password)
    except ValueError as exc:
        return templates.TemplateResponse(
            request,
            "activate.html",
            {"request": request, "token": token, "error": str(exc)},
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
    consent: str = Form(""),
):
    try:
        email = require_email(email)
        enforce_password_login(request, email)
        if consent != "yes":
            raise ValueError("Please accept the privacy notice and terms to continue.")
        if len(password.strip()) < 4:
            raise ValueError("Password must be at least 4 characters.")
    except ValueError as exc:
        return redirect_to(path_with_query("/login", error=str(exc)))

    account = data.authenticate_account(email, password)
    if account is None:
        record_password_login(request, email, False)
        return redirect_to(path_with_query("/login", error="Invalid email or password."))
    record_password_login(request, email, True)

    if LOGIN_OTP_ENABLED:
        try:
            return begin_login_otp(request, account, "password_otp")
        except ValueError as exc:
            return redirect_to(path_with_query("/login", error=str(exc)))

    data.record_user_consent(account["account_id"], CONSENT_VERSION, "password_login")
    return establish_portal_session(request, account)


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
        data.record_user_consent(
            account["account_id"],
            request.session.get("pending_login_consent_version") or CONSENT_VERSION,
            request.session.get("pending_login_consent_source") or "password_otp",
        )
    except (TypeError, ValueError) as exc:
        record_event("otp_verify", f"account:{account_id}")
        return redirect_to(path_with_query("/login", error=str(exc)))
    record_event("otp_verify", f"account:{account_id}", succeeded=True)
    return establish_portal_session(request, account)


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    response = redirect_to(path_with_query("/login"))
    return response



