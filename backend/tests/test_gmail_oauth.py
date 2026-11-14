import base64
import pytest
from email import message_from_bytes
from email.policy import default
from types import SimpleNamespace

from cryptography.fernet import Fernet

from app.extensions import db
from app.models import GmailConnection, User
from app.services.auth import seed_admin
from app.services.gmail_oauth import _prepare_oauth_transport


def test_gmail_localhost_http_transport_allowed_only_in_development(app, monkeypatch):
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
    app.config["DEBUG"] = True
    with app.app_context():
        _prepare_oauth_transport("http://localhost:5000/api/v1/admin/google-mail/callback")
    assert __import__("os").environ.get("OAUTHLIB_INSECURE_TRANSPORT") == "1"


def test_gmail_production_requires_https(app, monkeypatch):
    monkeypatch.delenv("OAUTHLIB_INSECURE_TRANSPORT", raising=False)
    app.config.update(DEBUG=False, TESTING=False)
    with app.app_context():
        _prepare_oauth_transport("https://portal.example.com/api/v1/admin/google-mail/callback")
        try:
            _prepare_oauth_transport("http://localhost:5000/api/v1/admin/google-mail/callback")
            assert False, "HTTP Gmail OAuth callback must be rejected in production"
        except Exception as exc:
            assert "HTTPS" in str(exc)
    assert "OAUTHLIB_INSECURE_TRANSPORT" not in __import__("os").environ


def _login_admin(app, client):
    with app.app_context():
        seed_admin("admin@example.com", "StrongPassword123!")
    return client.post("/api/v1/auth/login", json={
        "email": "admin@example.com", "password": "StrongPassword123!", "role": "admin",
    })


def test_gmail_routes_require_admin_and_status_is_safe(app, client):
    assert client.get("/api/v1/admin/google-mail/status").status_code == 401
    with app.app_context():
        examiner = User(full_name="Examiner", email="examiner@example.com", role="examiner")
        examiner.set_password("StrongPassword123!")
        db.session.add(examiner)
        db.session.commit()
    examiner_client = app.test_client()
    examiner_client.post("/api/v1/auth/login", json={
        "email": "examiner@example.com", "password": "StrongPassword123!", "role": "examiner",
    })
    assert examiner_client.get("/api/v1/admin/google-mail/status").status_code == 403
    _login_admin(app, client)
    app.config.update(
        GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="client-secret",
        GOOGLE_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    response = client.get("/api/v1/admin/google-mail/status")
    assert response.status_code == 200
    assert response.json == {
        "connected": False, "email": None, "status": "disconnected", "configured": True,
    }
    assert not {"token", "refresh_token", "client_secret", "credentials"} & response.json.keys()


def test_oauth_state_is_consumed_and_callback_errors_are_handled(app, client, monkeypatch):
    _login_admin(app, client)
    app.config.update(
        GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="client-secret",
        GOOGLE_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    monkeypatch.setattr("app.api.google_mail_routes.gmail_oauth.authorization_url", lambda state: "https://accounts.example/authorize")
    start = client.get("/api/v1/admin/google-mail/connect")
    assert start.status_code == 302
    with client.session_transaction() as session:
        state = session["google_mail_oauth_state"]

    invalid = client.get("/api/v1/admin/google-mail/callback?code=code&state=wrong")
    assert invalid.status_code == 302 and "gmail=error" in invalid.location
    with client.session_transaction() as session:
        assert "google_mail_oauth_state" not in session

    with client.session_transaction() as session:
        session["google_mail_oauth_state"] = state
    cancelled = client.get(f"/api/v1/admin/google-mail/callback?error=access_denied&state={state}")
    assert cancelled.status_code == 302 and "gmail=cancelled" in cancelled.location


def test_oauth_callback_saves_connected_account_without_returning_credentials(app, client, monkeypatch):
    _login_admin(app, client)
    app.config.update(
        GOOGLE_CLIENT_ID="client-id", GOOGLE_CLIENT_SECRET="client-secret",
        GOOGLE_TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    received = {}
    monkeypatch.setattr("app.api.google_mail_routes.gmail_oauth.complete_authorization", lambda *args: received.update(args=args))
    with client.session_transaction() as session:
        session["google_mail_oauth_state"] = "valid-state"
        session["google_mail_oauth_code_verifier"] = "pkce-verifier"
    response = client.get("/api/v1/admin/google-mail/callback?code=code&state=valid-state")
    assert response.status_code == 302 and "gmail=connected" in response.location
    assert received["args"][2] == "pkce-verifier"


def test_token_encryption_round_trip_and_disconnect(app, client):
    _login_admin(app, client)
    app.config["GOOGLE_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    from app.services.gmail_oauth import _decrypt, _encrypt, save_credentials

    raw_credentials = '{"token":"access-secret","refresh_token":"refresh-secret"}'
    encrypted = _encrypt(raw_credentials)
    assert raw_credentials not in encrypted
    assert _decrypt(encrypted) == raw_credentials
    with app.app_context():
        save_credentials("sender@example.com", raw_credentials)
        connection = db.session.get(GmailConnection, 1)
        assert connection.status == "connected"
        assert connection.account_email == "sender@example.com"
        assert "access-secret" not in connection.encrypted_credentials
    safe_status = client.get("/api/v1/admin/google-mail/status").json
    assert safe_status["connected"] is True and safe_status["email"] == "sender@example.com"
    assert not {"token", "refresh_token", "client_secret", "credentials"} & safe_status.keys()
    disconnected = client.post("/api/v1/admin/google-mail/disconnect")
    assert disconnected.status_code == 200 and disconnected.json["status"] == "disconnected"
    with app.app_context():
        connection = db.session.get(GmailConnection, 1)
        assert connection.status == "disconnected"
        assert connection.account_email is None and connection.encrypted_credentials is None
    assert client.get("/api/v1/admin/google-mail/status").json["connected"] is False


def test_gmail_scope_and_refresh_token_requirements(app):
    from app.services.gmail_oauth import (GMAIL_SEND_SCOPE, USERINFO_EMAIL_SCOPE, _decrypt,
                                          save_credentials, validate_granted_scopes)
    app.config["GOOGLE_TOKEN_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
    assert GMAIL_SEND_SCOPE in __import__("app.services.gmail_oauth", fromlist=["OAUTH_SCOPES"]).OAUTH_SCOPES
    assert validate_granted_scopes(["email", "openid", GMAIL_SEND_SCOPE]) == {"openid", USERINFO_EMAIL_SCOPE, GMAIL_SEND_SCOPE}
    with pytest.raises(Exception, match="missing required"):
        validate_granted_scopes(["email", "openid"])
    with pytest.raises(Exception, match="unexpected"):
        validate_granted_scopes(["email", "openid", GMAIL_SEND_SCOPE, "https://mail.google.com/"])
    with app.app_context():
        save_credentials("sender@example.com", '{"token":"access","refresh_token":"existing-refresh"}')
        save_credentials("sender@example.com", '{"token":"new-access"}')
        stored = db.session.get(GmailConnection, 1)
        assert "existing-refresh" in _decrypt(stored.encrypted_credentials)


def test_gmail_email_uses_api_message_send(app, monkeypatch):
    import app.services.gmail_email as gmail_email

    app.config["EMAIL_PROVIDER"] = "gmail_api"
    credentials = SimpleNamespace(valid=True, expired=False, refresh_token=None)
    monkeypatch.setattr(gmail_email, "load_credentials", lambda: credentials)
    calls = {}

    class Sender:
        def execute(self):
            return {"id": "message-id"}

    class Messages:
        def send(self, userId, body):
            calls.update(userId=userId, raw=body["raw"])
            return Sender()

    class Users:
        def messages(self):
            return Messages()

    fake_service = SimpleNamespace(users=lambda: Users())
    monkeypatch.setattr(gmail_email, "_gmail_service", lambda _credentials: fake_service, raising=False)
    monkeypatch.setattr("google.auth.transport.requests.Request", lambda: object())
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *args, **kwargs: fake_service)

    assert gmail_email.send_gmail_email("student@example.com", "Exam notice", "Your slot is tomorrow.")["delivered"] is True
    parsed = message_from_bytes(base64.urlsafe_b64decode(calls["raw"]), policy=default)
    assert calls["userId"] == "me"
    assert parsed["To"] == "student@example.com"
    assert parsed["Subject"] == "Exam notice"
    assert parsed.get_content().strip() == "Your slot is tomorrow."


def test_gmail_refresh_persists_credentials_and_smtp_path_remains_available(app, monkeypatch):
    import app.services.email as email_service
    import app.services.gmail_email as gmail_email

    app.config.update(EMAIL_PROVIDER="gmail_api", SMTP_HOST=None)
    credentials = SimpleNamespace(valid=False, expired=True, refresh_token="refresh")
    credentials.refresh = lambda _request: setattr(credentials, "valid", True)
    monkeypatch.setattr(gmail_email, "load_credentials", lambda: credentials)
    persisted = []
    monkeypatch.setattr(gmail_email, "persist_refreshed_credentials", lambda creds: persisted.append(creds))
    service = SimpleNamespace(users=lambda: SimpleNamespace(messages=lambda: SimpleNamespace(
        send=lambda **_kwargs: SimpleNamespace(execute=lambda: {})
    )))
    monkeypatch.setattr("google.auth.transport.requests.Request", lambda: object())
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *args, **kwargs: service)
    assert email_service.send_email("student@example.com", "Subject", "Body")["delivered"] is True
    assert persisted == [credentials]

    app.config.update(EMAIL_PROVIDER="smtp", SMTP_HOST="smtp.test", SMTP_PORT=587, SMTP_FROM="portal@example.com", SMTP_USERNAME=None)
    import smtplib
    sent = []

    class FakeSMTP:
        def __init__(self, *_args, **_kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *_args): pass
        def starttls(self): pass
        def send_message(self, message): sent.append(message)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    assert email_service.send_email("student@example.com", "Subject", "Body")["delivered"] is True
    assert sent[0]["To"] == "student@example.com" and sent[0].get_content().strip() == "Body"


def test_failed_gmail_refresh_requires_reauthorization(app, monkeypatch):
    import app.services.gmail_email as gmail_email

    credentials = SimpleNamespace(valid=False, expired=True, refresh_token="revoked-refresh-token")
    credentials.refresh = lambda _request: (_ for _ in ()).throw(RuntimeError("provider details must not escape"))
    monkeypatch.setattr(gmail_email, "load_credentials", lambda: credentials)
    marked = []
    monkeypatch.setattr(gmail_email, "mark_reauthorization_required", lambda: marked.append(True))
    monkeypatch.setattr("google.auth.transport.requests.Request", lambda: object())
    with app.app_context():
        try:
            gmail_email.send_gmail_email("student@example.com", "Subject", "Body")
            assert False, "A failed credential refresh should stop delivery"
        except gmail_email.GmailDeliveryError as exc:
            assert "reconnected" in str(exc)
            assert "provider details" not in str(exc)
    assert marked == [True]
