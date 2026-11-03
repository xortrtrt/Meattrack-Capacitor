from __future__ import annotations

from html import escape
import json
from urllib import error, request

from app.config import (
    BREVO_API_KEY,
    BREVO_API_URL,
    BREVO_FROM_EMAIL,
    BREVO_FROM_NAME,
)


def brevo_ready() -> bool:
    return bool(BREVO_API_KEY and BREVO_API_URL and BREVO_FROM_EMAIL)


def email_ready() -> bool:
    return brevo_ready()


def _send_via_brevo(*, to_email: str, subject: str, body: str, html_body: str | None = None) -> None:
    message = {
        "sender": {
            "name": BREVO_FROM_NAME or "Batangas Premium",
            "email": BREVO_FROM_EMAIL,
        },
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": body,
    }
    if html_body:
        message["htmlContent"] = html_body
    payload = json.dumps(message).encode("utf-8")
    http_request = request.Request(
        BREVO_API_URL,
        data=payload,
        method="POST",
        headers={
            "api-key": BREVO_API_KEY,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with request.urlopen(http_request, timeout=15) as response:
        if response.status >= 400:
            raise RuntimeError(f"Brevo returned HTTP {response.status}")
        response_body = response.read().decode("utf-8", errors="replace")
        try:
            result = json.loads(response_body or "{}")
        except json.JSONDecodeError:
            result = {}
        message_id = result.get("messageId") or "unavailable"
        print(f"Email delivery accepted via brevo: messageId={message_id} to={to_email}", flush=True)


def _log_email_failure(provider: str, exc: Exception) -> None:
    detail = f"HTTP {exc.code}" if isinstance(exc, error.HTTPError) else exc.__class__.__name__
    if isinstance(exc, error.HTTPError):
        response_body = exc.read().decode("utf-8", errors="replace")[:500]
        if response_body:
            detail = f"{detail}: {response_body}"
    if isinstance(exc, error.URLError) and getattr(exc, "reason", None):
        detail = f"URL error: {exc.reason.__class__.__name__}"
    print(f"Email delivery failed via {provider}: {detail}", flush=True)


def _send_email(
    *, to_email: str, subject: str, body: str, failure_message: str, success_message: str, html_body: str | None = None
) -> tuple[bool, str]:
    if brevo_ready():
        try:
            _send_via_brevo(to_email=to_email, subject=subject, body=body, html_body=html_body)
        except Exception as exc:
            _log_email_failure("brevo", exc)
            return False, failure_message
        return True, success_message

    return False, "Brevo email delivery is not configured."


def send_password_change_otp(*, to_email: str, name: str, otp_code: str) -> tuple[bool, str]:
    return _send_email(
        to_email=to_email,
        subject="Batangas Premium password change OTP",
        body="\n".join(
            [
                f"Hello {name},",
                "",
                "Use this one-time password to verify your email before choosing a new portal password:",
                "",
                otp_code,
                "",
                "This OTP expires in 10 minutes. If you did not request this change, keep your current password and contact your team leader.",
                "",
                "Batangas Premium",
            ]
        ),
        failure_message="Password OTP email could not be sent.",
        success_message="Password OTP email sent.",
    )


def send_login_otp(*, to_email: str, name: str, otp_code: str, purpose: str = "login") -> tuple[bool, str]:
    registration = purpose == "reseller_registration"
    return _send_email(
        to_email=to_email,
        subject="MEATTRACK reseller registration OTP" if registration else "Batangas Premium portal login OTP",
        body="\n".join(
            [
                f"Hello {name},",
                "",
                "Use this one-time password to verify your email and finish reseller registration:"
                if registration else "Use this one-time password to finish signing in to MEATTRACK:",
                "",
                otp_code,
                "",
                "This OTP expires in 10 minutes. If you did not request this, ignore this email."
                if registration else "This OTP expires in 10 minutes. If you did not try to sign in, ignore this email.",
                "",
                "Batangas Premium",
            ]
        ),
        failure_message="Verification OTP email could not be sent." if registration else "Login OTP email could not be sent.",
        success_message="Verification OTP email sent." if registration else "Login OTP email sent.",
    )


def send_reseller_registration_invitation(*, to_email: str, invitation_url: str) -> tuple[bool, str]:
    safe_url = escape(invitation_url, quote=True)
    return _send_email(
        to_email=to_email,
        subject="Your MEATTRACK reseller registration invitation",
        body="\n".join([
            "The Batangas Premium Business Owner invited you to create an approved reseller account.",
            "Use this single-use link within 7 days:", "", invitation_url, "",
            "The link is bound to this email address. You will verify this inbox with an OTP before the account is activated.",
            "If you were not expecting this invitation, ignore this email.", "", "Batangas Premium",
        ]),
        html_body=(
            "<p>The Batangas Premium Business Owner invited you to create an approved reseller account.</p>"
            f'<p><a href="{safe_url}">Create your reseller account</a></p>'
            "<p>This single-use invitation expires in 7 days and is bound to this email address. "
            "You will verify this inbox with an OTP before the account is activated.</p>"
            "<p>If you were not expecting this invitation, ignore this email.</p>"
        ),
        failure_message="Reseller invitation email could not be sent.",
        success_message="Reseller invitation email sent.",
    )


def send_owner_step_up_otp(*, to_email: str, name: str, otp_code: str) -> tuple[bool, str]:
    return _send_email(
        to_email=to_email,
        subject="MEATTRACK owner action verification",
        body="\n".join(
            [
                f"Hello {name},",
                "",
                "Use this one-time password to unlock sensitive owner actions:",
                "",
                otp_code,
                "",
                "This OTP expires in 10 minutes. Do not share it with anyone.",
                "If you did not request this code, sign out and contact another owner.",
                "",
                "Batangas Premium",
            ]
        ),
        failure_message="Owner verification email could not be sent.",
        success_message="Owner verification email sent.",
    )


def send_account_password_reset(*, to_email: str, name: str, reset_url: str) -> tuple[bool, str]:
    safe_name = escape(name)
    safe_url = escape(reset_url, quote=True)
    return _send_email(
        to_email=to_email,
        subject="Reset your MEATTRACK password",
        body="\n".join([
            f"Hello {name},", "", "An owner requested a password reset for your MEATTRACK account.",
            f"Set a new password within 24 hours: {reset_url}", "",
            "If you were not expecting this, contact an owner before using the link.", "", "Batangas Premium",
        ]),
        html_body=(
            f"<p>Hello {safe_name},</p><p>An owner requested a password reset for your MEATTRACK account.</p>"
            f'<p><a href="{safe_url}">Set a new password</a></p>'
            "<p>This single-use link expires in 24 hours.</p>"
        ),
        failure_message="Password reset email could not be sent.",
        success_message="Password reset email sent.",
    )


def send_account_deactivation_notice(*, to_email: str, name: str, reason: str) -> tuple[bool, str]:
    safe_name = escape(name)
    safe_reason = escape(reason).replace("\n", "<br>")
    return _send_email(
        to_email=to_email,
        subject="Your Batangas Premium portal access was deactivated",
        body="\n".join(
            [
                f"Hello {name},",
                "",
                "An Owner has deactivated access to your Batangas Premium portal account.",
                "",
                "Reason:",
                reason,
                "",
                "If you have questions or believe this was done in error, contact the Business Owner.",
                "",
                "Batangas Premium",
            ]
        ),
        html_body=(
            f"<p>Hello {safe_name},</p>"
            "<p>An Owner has deactivated access to your Batangas Premium portal account.</p>"
            f"<p><strong>Reason:</strong><br>{safe_reason}</p>"
            "<p>If you have questions or believe this was done in error, contact the Business Owner.</p>"
            "<p>Batangas Premium</p>"
        ),
        failure_message="Account deactivation notice could not be sent.",
        success_message="Account deactivation notice sent.",
    )


def send_account_reactivation_notice(*, to_email: str, name: str, reason: str) -> tuple[bool, str]:
    safe_name = escape(name)
    safe_reason = escape(reason).replace("\n", "<br>")
    return _send_email(
        to_email=to_email,
        subject="Your Batangas Premium portal access was restored",
        body="\n".join([
            f"Hello {name},", "",
            "An Owner has reactivated your Batangas Premium portal account.",
            "You can sign in again using your existing password.", "",
            "Reason:", reason, "",
            "If you have questions, contact the Business Owner.", "", "Batangas Premium",
        ]),
        html_body=(
            f"<p>Hello {safe_name},</p>"
            "<p>An Owner has reactivated your Batangas Premium portal account.</p>"
            "<p>You can sign in again using your existing password.</p>"
            f"<p><strong>Reason:</strong><br>{safe_reason}</p>"
            "<p>If you have questions, contact the Business Owner.</p><p>Batangas Premium</p>"
        ),
        failure_message="Account reactivation notice could not be sent.",
        success_message="Account reactivation notice sent.",
    )


def send_inquiry_status_update(*, to_email: str, name: str, business_name: str) -> tuple[bool, str]:
    return _send_email(
        to_email=to_email,
        subject="Batangas Premium reseller application update",
        body="\n".join(
            [
                f"Hello {name},",
                "",
                f"Your reseller inquiry for {business_name} is still under review.",
                "A Batangas Premium sales team leader will contact you once your application has been processed.",
                "",
                "Thank you for your patience.",
                "",
                "Batangas Premium",
            ]
        ),
        failure_message="Inquiry update email could not be sent.",
        success_message="Inquiry update email sent.",
    )


def send_inquiry_rejection(
    *,
    to_email: str,
    name: str,
    business_name: str,
    rejection_reason: str,
) -> tuple[bool, str]:
    safe_name = escape(name)
    safe_business_name = escape(business_name)
    safe_reason = escape(rejection_reason).replace("\n", "<br>")
    return _send_email(
        to_email=to_email,
        subject="Batangas Premium reseller application decision",
        body="\n".join(
            [
                f"Hello {name},",
                "",
                f"We reviewed your reseller inquiry for {business_name} and cannot approve it at this time.",
                "",
                "Reason:",
                rejection_reason,
                "",
                "You may contact Batangas Premium if you need clarification or want to submit a new inquiry later.",
                "",
                "Batangas Premium",
            ]
        ),
        html_body=(
            f"<p>Hello {safe_name},</p>"
            f"<p>We reviewed your reseller inquiry for {safe_business_name} and cannot approve it at this time.</p>"
            f"<p><strong>Reason:</strong><br>{safe_reason}</p>"
            "<p>You may contact Batangas Premium if you need clarification or want to submit a new inquiry later.</p>"
            "<p>Batangas Premium</p>"
        ),
        failure_message="Inquiry rejection email could not be sent.",
        success_message="Inquiry rejection email sent.",
    )


def send_account_activation(*, to_email: str, name: str, account_label: str, activation_url: str) -> tuple[bool, str]:
    safe_name = escape(name)
    safe_label = escape(account_label)
    safe_url = escape(activation_url, quote=True)
    return _send_email(
        to_email=to_email,
        subject="Activate your Batangas Premium portal account",
        body="\n".join(
            [
                f"Hello {name},",
                "",
                f"Your Batangas Premium {account_label} account is ready.",
                "Use this single-use link within 24 hours to create your password:",
                "",
                activation_url,
                "",
                "If you did not expect this account, ignore this email and contact Batangas Premium.",
                "",
                "Batangas Premium",
            ]
        ),
        failure_message="Activation email could not be sent.",
        success_message="Activation email sent.",
        html_body=(
            f"<p>Hello {safe_name},</p>"
            f"<p>Your Batangas Premium {safe_label} account is ready.</p>"
            "<p>Use this single-use link within 24 hours to create your password:</p>"
            f'<p><a href="{safe_url}">Activate your account</a></p>'
            "<p>If you did not expect this account, ignore this email and contact Batangas Premium.</p>"
        ),
    )
