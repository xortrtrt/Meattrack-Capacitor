from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.config import SMTP_FROM_EMAIL, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USERNAME


def smtp_ready() -> bool:
    return bool(SMTP_HOST and SMTP_PORT and SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM_EMAIL)


def send_reseller_credentials(
    *,
    to_email: str,
    business_name: str,
    temporary_password: str,
    team_leader_name: str,
) -> tuple[bool, str]:
    if not smtp_ready():
        return False, "SMTP is not configured."

    message = EmailMessage()
    message["Subject"] = "Batangas Premium reseller portal access"
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = to_email
    message.set_content(
        "\n".join(
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
        )
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
    except Exception:
        return False, "Credential email could not be sent."

    return True, "Credential email sent."


def send_password_change_otp(*, to_email: str, name: str, otp_code: str) -> tuple[bool, str]:
    if not smtp_ready():
        return False, "SMTP is not configured."

    message = EmailMessage()
    message["Subject"] = "Batangas Premium password change OTP"
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = to_email
    message.set_content(
        "\n".join(
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
        )
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
    except Exception:
        return False, "Password OTP email could not be sent."

    return True, "Password OTP email sent."


def send_login_otp(*, to_email: str, name: str, otp_code: str) -> tuple[bool, str]:
    if not smtp_ready():
        return False, "SMTP is not configured."

    message = EmailMessage()
    message["Subject"] = "Batangas Premium portal login OTP"
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = to_email
    message.set_content(
        "\n".join(
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
        )
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
    except Exception:
        return False, "Login OTP email could not be sent."

    return True, "Login OTP email sent."


def send_portal_credentials(
    *,
    to_email: str,
    name: str,
    temporary_password: str,
    account_label: str,
) -> tuple[bool, str]:
    if not smtp_ready():
        return False, "SMTP is not configured."

    message = EmailMessage()
    message["Subject"] = "Batangas Premium portal account created"
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = to_email
    message.set_content(
        "\n".join(
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
        )
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
    except Exception:
        return False, "Credential email could not be sent."

    return True, "Credential email sent."


def send_inquiry_status_update(*, to_email: str, name: str, business_name: str) -> tuple[bool, str]:
    if not smtp_ready():
        return False, "SMTP is not configured."

    message = EmailMessage()
    message["Subject"] = "Batangas Premium reseller application update"
    message["From"] = SMTP_FROM_EMAIL
    message["To"] = to_email
    message.set_content(
        "\n".join(
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
        )
    )

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
            server.starttls()
            server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
    except Exception:
        return False, "Inquiry update email could not be sent."

    return True, "Inquiry update email sent."
