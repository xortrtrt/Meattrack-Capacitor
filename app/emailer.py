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
