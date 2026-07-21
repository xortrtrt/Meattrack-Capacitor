from __future__ import annotations

import json
import smtplib
from email.message import EmailMessage
from html import escape
from urllib import error, request

from app.config import (
    BREVO_API_KEY,
    BREVO_API_URL,
    BREVO_FROM_EMAIL,
    BREVO_FROM_NAME,
    RESEND_API_KEY,
    RESEND_API_URL,
    RESEND_FROM_EMAIL,
    SMTP_FROM_EMAIL,
    SMTP_HOST,
    SMTP_PASSWORD,
    SMTP_PORT,
    SMTP_USERNAME,
)


def smtp_ready() -> bool:
    return bool(SMTP_HOST and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM_EMAIL)


def brevo_ready() -> bool:
    return bool(BREVO_API_KEY and BREVO_API_URL and BREVO_FROM_EMAIL)


def resend_ready() -> bool:
    return bool(RESEND_API_KEY and RESEND_API_URL and RESEND_FROM_EMAIL)


def email_ready() -> bool:
    return brevo_ready() or resend_ready() or smtp_ready()


def _send_via_brevo(*, to_email: str, subject: str, body: str) -> None:
    payload = json.dumps(
        {
            "sender": {
                "name": BREVO_FROM_NAME or "Batangas Premium",
                "email": BREVO_FROM_EMAIL,
            },
            "to": [{"email": to_email}],
            "subject": subject,
            "textContent": body,
        }
    ).encode("utf-8")
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


def _send_via_resend(*, to_email: str, subject: str, body: str) -> None:
    payload = json.dumps(
        {
            "from": RESEND_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "text": body,
            "html": f'<pre style="font-family: sans-serif; white-space: pre-wrap;">{escape(body)}</pre>',
        }
    ).encode("utf-8")
    http_request = request.Request(
        RESEND_API_URL,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with request.urlopen(http_request, timeout=15) as response:
        if response.status >= 400:
            raise RuntimeError(f"Resend returned HTTP {response.status}")


def _send_via_smtp(*, to_email: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = to_email
    message.set_content(body)

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(SMTP_USERNAME, SMTP_PASSWORD)
        server.send_message(message)


def _log_email_failure(provider: str, exc: Exception) -> None:
    detail = f"HTTP {exc.code}" if isinstance(exc, error.HTTPError) else exc.__class__.__name__
    if isinstance(exc, error.URLError) and getattr(exc, "reason", None):
        detail = f"URL error: {exc.reason.__class__.__name__}"
    print(f"Email delivery failed via {provider}: {detail}", flush=True)


def _send_email(*, to_email: str, subject: str, body: str, failure_message: str, success_message: str) -> tuple[bool, str]:
    if brevo_ready():
        try:
            _send_via_brevo(to_email=to_email, subject=subject, body=body)
        except Exception as exc:
            _log_email_failure("brevo", exc)
            return False, failure_message
        return True, success_message

    if resend_ready():
        try:
            _send_via_resend(to_email=to_email, subject=subject, body=body)
        except Exception as exc:
            _log_email_failure("resend", exc)
            return False, failure_message
        return True, success_message

    if smtp_ready():
        try:
            _send_via_smtp(to_email=to_email, subject=subject, body=body)
        except Exception as exc:
            _log_email_failure("smtp", exc)
            return False, failure_message
        return True, success_message

    return False, "Email delivery is not configured."


def send_reseller_credentials(
    *,
    to_email: str,
    business_name: str,
    temporary_password: str,
    team_leader_name: str,
) -> tuple[bool, str]:
    return _send_email(
        to_email=to_email,
        subject="Batangas Premium reseller portal access",
        body="\n".join(
            [
                f"Hello {business_name},",
                "",
                "Your Batangas Premium reseller portal account has been created.",
                "",
                f"Email: {to_email}",
                f"Temporary password: {temporary_password}",
                "",
                "Please sign in and change your password after first access if the portal asks you to do so.",
                f"Assigned team leader: {team_leader_name}",
                "",
                "Batangas Premium",
            ]
        ),
        failure_message="Credential email could not be sent.",
        success_message="Credential email sent.",
    )


def send_password_change_otp(*, to_email: str, name: str, otp_code: str) -> tuple[bool, str]:
    return _send_email(
        to_email=to_email,
        subject="Batangas Premium password change OTP",
        body="\n".join(
            [
                f"Hello {name},",
                "",
                "Use this one-time password to confirm your Batangas Premium reseller portal password change:",
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


def send_login_otp(*, to_email: str, name: str, otp_code: str) -> tuple[bool, str]:
    return _send_email(
        to_email=to_email,
        subject="Batangas Premium portal login OTP",
        body="\n".join(
            [
                f"Hello {name},",
                "",
                "Use this one-time password to finish signing in to MEATTRACK:",
                "",
                otp_code,
                "",
                "This OTP expires in 10 minutes. If you did not try to sign in, ignore this email.",
                "",
                "Batangas Premium",
            ]
        ),
        failure_message="Login OTP email could not be sent.",
        success_message="Login OTP email sent.",
    )


def send_portal_credentials(
    *,
    to_email: str,
    name: str,
    temporary_password: str,
    account_label: str,
) -> tuple[bool, str]:
    return _send_email(
        to_email=to_email,
        subject="Batangas Premium portal account created",
        body="\n".join(
            [
                f"Hello {name},",
                "",
                f"Your Batangas Premium {account_label} portal account has been created.",
                "",
                f"Email: {to_email}",
                f"Temporary password: {temporary_password}",
                "",
                "When you sign in, an OTP will be sent to this email for confirmation.",
                "After signing in, open Profile to change your password.",
                "",
                "Batangas Premium",
            ]
        ),
        failure_message="Credential email could not be sent.",
        success_message="Credential email sent.",
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
