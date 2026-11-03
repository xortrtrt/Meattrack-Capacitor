from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request

from app.capabilities import require_capability
from app.portals import shared
from app.repositories import password_changes as data

router = APIRouter()
SESSION_KEY = "password_change_verification"
ROLES = {"owner", "reseller", "team-leader", "sales-supervisor"}


def guard(request: Request, role_key: str):
    if role_key not in ROLES:
        raise HTTPException(status_code=404)
    result = shared.require_portal_session(request, role_key)
    if not result:
        require_capability(request, "profile.manage_own", role_key)
    return result


def state(request: Request) -> dict:
    value = request.session.get(SESSION_KEY, {})
    return value if value.get("account_id") == shared.session_account_id(request) else {}


def context(request: Request) -> dict:
    pending = state(request)
    stage = (
        data.password_change_stage(pending["account_id"], pending["challenge_id"], pending.get("grant", ""))
        if pending
        else "request"
    )
    return {"password_change_stage": stage}


def back(role_key: str, *, error: str = "", message: str = ""):
    return shared.redirect_to(shared.safe_portal_path(role_key, "profile", error=error, message=message))


@router.post("/portal/{role_key}/profile/password")
async def request_code(request: Request, role_key: str, current_password: Annotated[str, Form()]):
    denied = guard(request, role_key)
    if denied:
        return denied
    account_id = shared.session_account_id(request)
    try:
        pending = data.request_password_verification(account_id, current_password)
        request.session.pop(SESSION_KEY, None)
        try:
            sent, message = await asyncio.to_thread(
                shared.send_password_change_otp,
                to_email=pending["email"],
                name=pending["name"],
                otp_code=pending["otp_code"],
            )
        except Exception:
            sent, message = False, "Password OTP email could not be sent. Please try again."
        if not sent:
            data.cancel_password_verification(account_id, pending["challenge_id"])
            return back(role_key, error=message)
        request.session[SESSION_KEY] = {"account_id": account_id, "challenge_id": pending["challenge_id"]}
    except ValueError as exc:
        return back(role_key, error=str(exc))
    return back(role_key, message="OTP sent to your account email. Verify it before choosing a new password.")


@router.post("/portal/{role_key}/profile/password/confirm")
async def verify_code(request: Request, role_key: str, otp_code: Annotated[str, Form()]):
    denied = guard(request, role_key)
    if denied:
        return denied
    pending = state(request)
    if not pending:
        return back(role_key, error="Request a password OTP first.")
    try:
        grant = data.verify_password_challenge(pending["account_id"], pending["challenge_id"], otp_code)
    except ValueError as exc:
        return back(role_key, error=str(exc))
    request.session[SESSION_KEY] = {**pending, "grant": grant}
    return back(role_key, message="Email verified. You can now choose your new password.")


@router.post("/portal/{role_key}/profile/password/save")
async def save_password(
    request: Request, role_key: str, new_password: Annotated[str, Form()], confirm_password: Annotated[str, Form()]
):
    denied = guard(request, role_key)
    if denied:
        return denied
    pending = state(request)
    try:
        if not pending or not pending.get("grant"):
            raise ValueError("Verify an email OTP before changing your password.")
        if new_password != confirm_password:
            raise ValueError("New password and confirmation do not match.")
        data.complete_verified_password_change(
            pending["account_id"], pending["challenge_id"], pending["grant"], new_password
        )
    except ValueError as exc:
        return back(role_key, error=str(exc))
    request.session.clear()
    return shared.redirect_to(shared.path_with_query("/login", message="Password updated. Sign in again."))


@router.post("/portal/{role_key}/profile/password/cancel")
async def cancel(request: Request, role_key: str):
    denied = guard(request, role_key)
    if denied:
        return denied
    pending = state(request)
    if pending:
        data.cancel_password_verification(pending["account_id"], pending["challenge_id"])
    request.session.pop(SESSION_KEY, None)
    return back(role_key, message="Password change cancelled.")
