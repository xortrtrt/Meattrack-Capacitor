from __future__ import annotations

import json

from app import emailer, worker


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


def test_account_activation_includes_html_link(monkeypatch):
    calls = []
    monkeypatch.setattr(emailer, "BREVO_API_KEY", "xkeysib-test-key")
    monkeypatch.setattr(emailer, "BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
    monkeypatch.setattr(emailer, "BREVO_FROM_EMAIL", "sender@example.test")
    monkeypatch.setattr(
        emailer.request,
        "urlopen",
        lambda http_request, timeout=15: calls.append(http_request) or ResponseStub(),
    )

    sent, _ = emailer.send_account_activation(
        to_email="owner@example.test",
        name="Owner & Co.",
        account_label="sales <lead>",
        activation_url="https://portal.example.test/activate?token=a&next=b",
    )

    assert sent is True
    payload = json.loads(calls[0].data)
    assert payload["textContent"].endswith("https://portal.example.test/activate?token=a&next=b\n\nIf you did not expect this account, ignore this email and contact Batangas Premium.\n\nBatangas Premium")
    assert payload["htmlContent"] == (
        "<p>Hello Owner &amp; Co.,</p>"
        "<p>Your Batangas Premium sales &lt;lead&gt; account is ready.</p>"
        "<p>Use this single-use link within 24 hours to create your password:</p>"
        '<p><a href="https://portal.example.test/activate?token=a&amp;next=b">Activate your account</a></p>'
        "<p>If you did not expect this account, ignore this email and contact Batangas Premium.</p>"
    )


def test_inquiry_rejection_email_contains_safe_reason(monkeypatch):
    calls = []
    monkeypatch.setattr(emailer, "BREVO_API_KEY", "xkeysib-test-key")
    monkeypatch.setattr(emailer, "BREVO_API_URL", "https://api.brevo.com/v3/smtp/email")
    monkeypatch.setattr(emailer, "BREVO_FROM_EMAIL", "sender@example.test")
    monkeypatch.setattr(
        emailer.request,
        "urlopen",
        lambda http_request, timeout=15: calls.append(http_request) or ResponseStub(),
    )

    sent, message = emailer.send_inquiry_rejection(
        to_email="applicant@example.test",
        name="Ana & Sons",
        business_name="Ana's <Store>",
        rejection_reason="The submitted permit is incomplete.\nPlease provide the current permit.",
    )

    assert sent is True
    assert message == "Inquiry rejection email sent."
    payload = json.loads(calls[0].data)
    assert payload["subject"] == "Batangas Premium reseller application decision"
    assert "The submitted permit is incomplete." in payload["textContent"]
    assert "Ana &amp; Sons" in payload["htmlContent"]
    assert "Ana&#x27;s &lt;Store&gt;" in payload["htmlContent"]
    assert "incomplete.<br>Please provide" in payload["htmlContent"]


def test_worker_routes_rejection_outbox_to_rejection_email(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        worker,
        "send_inquiry_rejection",
        lambda **kwargs: captured.update(kwargs) or (True, "sent"),
    )

    result = worker._deliver({
        "event_type": "inquiry_rejected",
        "payload": {
            "to_email": "applicant@example.test",
            "name": "Ana",
            "business_name": "Ana Store",
            "rejection_reason": "Incomplete permit.",
        },
    })

    assert result == (True, "sent")
    assert captured["rejection_reason"] == "Incomplete permit."
