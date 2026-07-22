from __future__ import annotations

from app import emailer


class ResponseStub:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return b'{"messageId":"test-message-id"}'


def test_login_otp_uses_brevo_when_configured(monkeypatch):
    calls = []

    monkeypatch.setattr(emailer, "BREVO_API_KEY", "xkeysib-test-key")
    monkeypatch.setattr(emailer, "BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
    monkeypatch.setattr(emailer, "BREVO_FROM_EMAIL", "sender@example.test")
    monkeypatch.setattr(emailer, "BREVO_FROM_NAME", "Batangas Premium")
    monkeypatch.setattr(
        emailer.request,
        "urlopen",
        lambda http_request, timeout=15: calls.append((http_request, timeout)) or ResponseStub(),
    )

    sent, message = emailer.send_login_otp(
        to_email="owner@example.test",
        name="Owner",
        otp_code="123456",
    )

    assert sent is True
    assert message == "Login OTP email sent."
    http_request, timeout = calls[0]
    assert timeout == 15
    assert http_request.full_url == "https://api.brevo.com/v3/smtp/email"
    assert http_request.get_header("Api-key") == "xkeysib-test-key"
    body = http_request.data.decode("utf-8")
    assert '"sender": {"name": "Batangas Premium", "email": "sender@example.test"}' in body
    assert '"to": [{"email": "owner@example.test"}]' in body
    assert "123456" in body


def test_emailer_reports_unconfigured_brevo(monkeypatch):
    monkeypatch.setattr(emailer, "BREVO_API_KEY", "")
    monkeypatch.setattr(emailer, "BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
    monkeypatch.setattr(emailer, "BREVO_FROM_EMAIL", "")

    sent, message = emailer.send_password_change_otp(
        to_email="sales@example.test",
        name="Sales Leader",
        otp_code="987654",
    )

    assert sent is False
    assert message == "Brevo email delivery is not configured."
