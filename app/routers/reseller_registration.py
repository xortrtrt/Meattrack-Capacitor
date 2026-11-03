from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, Request

from app import repositories as data
from app import web
from app.config import PRIVACY_NOTICE_VERSION, TERMS_VERSION
from app.emailer import send_login_otp
from app.security_controls import enforce_otp_attempt, record_event


router = APIRouter()
templates = web.templates
redirect_to = web.redirect_to
path_with_query = web.path_with_query

PENDING_ACCOUNT_SESSION_KEY = "pending_reseller_registration_account_id"


def _render(request: Request, *, token: str = "", invitation: dict | None = None, error: str = "", message: str = ""):
    account_id = request.session.get(PENDING_ACCOUNT_SESSION_KEY)
    started = bool(account_id and invitation and invitation.get("registration_account_id"))
    return templates.TemplateResponse(
        request,
        "reseller_register.html",
        {
            "request": request,
            "token": token,
            "invitation": invitation or {},
            "registration_started": started,
            "error": error,
            "message": message,
            "privacy_notice_version": PRIVACY_NOTICE_VERSION,
            "terms_version": TERMS_VERSION,
        },
        status_code=404 if not invitation else 200,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


async def _send_registration_otp(account_id: int) -> tuple[bool, str]:
    pending = data.request_login_otp(account_id, purpose="reseller_registration")
    account = pending["account"]
    sent, message = await asyncio.to_thread(
        send_login_otp,
        to_email=account["email"],
        name=account["name"],
        otp_code=pending["otp_code"],
        purpose="reseller_registration",
    )
    if not sent:
        data.cancel_login_otp(account_id, pending.get("otp_id"))
    return sent, message


@router.get("/register/reseller")
async def reseller_registration_page(request: Request, token: str = ""):
    try:
        invitation = data.reseller_registration_invitation(token)
    except ValueError as exc:
        return _render(request, token=token, error=str(exc))
    if invitation.get("registration_account_id"):
        request.session[PENDING_ACCOUNT_SESSION_KEY] = int(invitation["registration_account_id"])
        invitation = data.reseller_registration_for_account(int(invitation["registration_account_id"]))
    return _render(request, token=token, invitation=invitation)


@router.post("/register/reseller")
async def submit_reseller_registration(
    request: Request,
    token: str = Form(...),
    full_name: str = Form(...),
    business_name: str = Form(...),
    contact_number: str = Form(...),
    address: str = Form(""),
    password: str = Form(...),
    confirm_password: str = Form(...),
    privacy_acknowledged: str = Form(""),
    terms_accepted: str = Form(""),
    privacy_notice_version: str = Form(""),
    terms_version: str = Form(""),
):
    try:
        if privacy_acknowledged != "yes" or terms_accepted != "yes":
            raise ValueError("Please review and acknowledge the Privacy Notice and Terms and Conditions.")
        if privacy_notice_version != PRIVACY_NOTICE_VERSION or terms_version != TERMS_VERSION:
            raise ValueError("The policy documents changed. Reload your invitation and review the current versions.")
        if password != confirm_password:
            raise ValueError("Password and confirmation do not match.")
        account = data.begin_invited_reseller_registration(
            token,
            full_name=full_name,
            business_name=business_name,
            contact_number=contact_number,
            address=address,
            password=password,
            privacy_notice_version=PRIVACY_NOTICE_VERSION,
            terms_version=TERMS_VERSION,
        )
        request.session[PENDING_ACCOUNT_SESSION_KEY] = int(account["account_id"])
        sent, mail_message = await _send_registration_otp(int(account["account_id"]))
        invitation = data.reseller_registration_for_account(int(account["account_id"]))
        if not sent:
            return _render(request, invitation=invitation, error=mail_message)
        return _render(
            request,
            invitation=invitation,
            message="We sent a 6-digit verification code to the invited email address.",
        )
    except ValueError as exc:
        try:
            invitation = data.reseller_registration_invitation(token)
        except ValueError:
            invitation = None
        if invitation and invitation.get("registration_account_id"):
            request.session[PENDING_ACCOUNT_SESSION_KEY] = int(invitation["registration_account_id"])
            invitation = data.reseller_registration_for_account(int(invitation["registration_account_id"]))
            token = ""
        return _render(request, token=token, invitation=invitation, error=str(exc))


@router.post("/register/reseller/verify")
async def verify_reseller_registration(request: Request, otp_code: str = Form(...)):
    account_id = request.session.get(PENDING_ACCOUNT_SESSION_KEY)
    if not account_id:
        return redirect_to(path_with_query("/login", error="Registration session expired. Reopen your invitation link."))
    try:
        account_id = int(account_id)
        enforce_otp_attempt(account_id, purpose="reseller_registration")
        data.confirm_login_otp(account_id, otp_code, purpose="reseller_registration")
    except (TypeError, ValueError) as exc:
        if str(exc) not in {"Too many incorrect OTP attempts. Request a new code."}:
            record_event("otp_verify", f"account:{account_id}:reseller_registration")
        try:
            invitation = data.reseller_registration_for_account(int(account_id))
        except ValueError:
            invitation = None
        return _render(request, invitation=invitation, error=str(exc))
    record_event("otp_verify", f"account:{account_id}:reseller_registration", succeeded=True)
    request.session.pop(PENDING_ACCOUNT_SESSION_KEY, None)
    return redirect_to(path_with_query("/login", message="Account Created"))


@router.post("/register/reseller/resend")
async def resend_reseller_registration_otp(request: Request):
    account_id = request.session.get(PENDING_ACCOUNT_SESSION_KEY)
    if not account_id:
        return redirect_to(path_with_query("/login", error="Registration session expired. Reopen your invitation link."))
    try:
        sent, message = await _send_registration_otp(int(account_id))
        invitation = data.reseller_registration_for_account(int(account_id))
        if not sent:
            return _render(request, invitation=invitation, error=message)
    except ValueError as exc:
        try:
            invitation = data.reseller_registration_for_account(int(account_id))
        except ValueError:
            invitation = None
        return _render(request, invitation=invitation, error=str(exc))
    return _render(request, invitation=invitation, message="A new verification code was sent to the invited email.")


__all__ = ["router"]
