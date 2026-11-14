"""Server-side OAuth credential handling for the configured Gmail sender."""
import json
import os
from urllib.parse import parse_qs, urlsplit

from flask import current_app
from cryptography.fernet import Fernet, InvalidToken

from ..extensions import db
from ..models import GmailConnection

GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
USERINFO_EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
EMAIL_IDENTITY_SCOPES = {"email", USERINFO_EMAIL_SCOPE}
OAUTH_SCOPES = [GMAIL_SEND_SCOPE, "openid", USERINFO_EMAIL_SCOPE]
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
LOCAL_OAUTH_HOSTS = {"localhost", "127.0.0.1", "::1"}


class GmailOAuthError(Exception):
    """A safe-to-handle Gmail OAuth configuration or credential error."""


def normalize_granted_scopes(scopes):
    """Canonicalize Google's equivalent email identity scope spellings."""
    return {USERINFO_EMAIL_SCOPE if scope in EMAIL_IDENTITY_SCOPES else scope for scope in (scopes or [])}


def validate_granted_scopes(scopes):
    normalized = normalize_granted_scopes(scopes)
    required = {"openid", USERINFO_EMAIL_SCOPE, GMAIL_SEND_SCOPE}
    allowed = required
    if not required.issubset(normalized):
        missing = required - normalized
        raise GmailOAuthError("Gmail authorization is missing required permission(s): " + ", ".join(sorted(missing)))
    unexpected = normalized - allowed
    if unexpected:
        raise GmailOAuthError("Gmail authorization returned unexpected permission(s)")
    return normalized


def _prepare_oauth_transport(redirect_uri):
    """Permit HTTP only for explicit local development/test Gmail OAuth."""
    parsed = urlsplit(redirect_uri)
    local_http = parsed.scheme == "http" and parsed.hostname in LOCAL_OAUTH_HOSTS
    if local_http and (current_app.debug or current_app.testing):
        os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
        return
    if parsed.scheme != "https":
        raise GmailOAuthError("Gmail OAuth requires an HTTPS callback outside local development")


def _fernet():
    key = current_app.config.get("GOOGLE_TOKEN_ENCRYPTION_KEY")
    if not key:
        raise GmailOAuthError("Gmail token encryption is not configured")
    try:
        return Fernet(key.encode("ascii") if isinstance(key, str) else key)
    except (ValueError, TypeError, UnicodeEncodeError) as exc:
        raise GmailOAuthError("Gmail token encryption key is invalid") from exc


def oauth_is_configured():
    if not (current_app.config.get("GOOGLE_CLIENT_ID") and
            current_app.config.get("GOOGLE_CLIENT_SECRET") and
            current_app.config.get("GOOGLE_REDIRECT_URI")):
        return False
    try:
        _fernet()
    except GmailOAuthError:
        return False
    return True


def _oauth_flow(state=None):
    if not oauth_is_configured():
        raise GmailOAuthError("Gmail OAuth is not configured")
    redirect_uri = current_app.config["GOOGLE_REDIRECT_URI"]
    _prepare_oauth_transport(redirect_uri)
    from google_auth_oauthlib.flow import Flow

    client_config = {"web": {
        "client_id": current_app.config["GOOGLE_CLIENT_ID"],
        "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": [redirect_uri],
    }}
    flow = Flow.from_client_config(client_config, scopes=OAUTH_SCOPES, state=state)
    flow.redirect_uri = redirect_uri
    return flow


def authorization_url(state):
    flow = _oauth_flow(state=state)
    url, _ = flow.authorization_url(
        state=state,
        access_type="offline",
        include_granted_scopes="false",
        prompt="consent",
    )
    return url, flow.code_verifier


def _encrypt(value):
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt(value):
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError, UnicodeEncodeError) as exc:
        raise GmailOAuthError("Stored Gmail credentials cannot be decrypted") from exc


def save_credentials(email, credentials_json):
    connection = db.session.get(GmailConnection, 1)
    if connection is None:
        connection = GmailConnection(id=1)
        db.session.add(connection)
    credentials_info = json.loads(credentials_json)
    if not credentials_info.get("refresh_token") and connection.encrypted_credentials:
        try:
            previous = json.loads(_decrypt(connection.encrypted_credentials))
            if previous.get("refresh_token"):
                credentials_info["refresh_token"] = previous["refresh_token"]
        except GmailOAuthError:
            raise
    if not credentials_info.get("refresh_token"):
        raise GmailOAuthError("Gmail authorization did not provide a refresh token")
    connection.account_email = email
    connection.encrypted_credentials = _encrypt(json.dumps(credentials_info))
    connection.status = "connected"
    db.session.commit()
    return connection


def complete_authorization(callback_url, state, code_verifier=None):
    flow = _oauth_flow(state=state)
    if not code_verifier:
        raise GmailOAuthError("Gmail OAuth code verifier is missing")
    flow.code_verifier = code_verifier
    query = parse_qs(urlsplit(callback_url).query)
    code = query.get("code", [None])[0]
    if not code:
        raise GmailOAuthError("Gmail authorization code is missing")
    flow.fetch_token(code=code)
    response = flow.oauth2session.get(USERINFO_URL, timeout=10)
    response.raise_for_status()
    identity = response.json()
    email = identity.get("email") if identity.get("email_verified") else None
    if not isinstance(email, str) or "@" not in email:
        raise GmailOAuthError("Google did not provide a verified account address")
    validate_granted_scopes(getattr(flow.credentials, "scopes", None))
    return save_credentials(email.strip().lower(), flow.credentials.to_json())


def connection_status():
    connection = db.session.get(GmailConnection, 1)
    connected = bool(connection and connection.status == "connected" and connection.encrypted_credentials)
    return {
        "connected": connected,
        "email": connection.account_email if connected else None,
        "status": connection.status if connection else ("disconnected" if oauth_is_configured() else "not_configured"),
        "configured": oauth_is_configured(),
    }


def load_credentials():
    connection = db.session.get(GmailConnection, 1)
    if not connection or connection.status != "connected" or not connection.encrypted_credentials:
        raise GmailOAuthError("No Gmail account is connected")
    from google.oauth2.credentials import Credentials

    try:
        credentials_info = json.loads(_decrypt(connection.encrypted_credentials))
        return Credentials.from_authorized_user_info(credentials_info, OAUTH_SCOPES)
    except GmailOAuthError:
        raise
    except Exception as exc:
        raise GmailOAuthError("Stored Gmail credentials are invalid") from exc


def persist_refreshed_credentials(credentials):
    connection = db.session.get(GmailConnection, 1)
    if not connection or connection.status != "connected":
        raise GmailOAuthError("No Gmail account is connected")
    connection.encrypted_credentials = _encrypt(credentials.to_json())
    db.session.commit()


def mark_reauthorization_required():
    connection = db.session.get(GmailConnection, 1)
    if connection and connection.status == "connected":
        connection.status = "reauthorization_required"
        db.session.commit()


def disconnect_connection():
    connection = db.session.get(GmailConnection, 1)
    refresh_token = None
    if connection and connection.encrypted_credentials:
        try:
            from google.oauth2.credentials import Credentials
            info = json.loads(_decrypt(connection.encrypted_credentials))
            credentials = Credentials.from_authorized_user_info(info, OAUTH_SCOPES)
            refresh_token = credentials.refresh_token
        except Exception:
            # Clear unusable local credentials even if they cannot be decrypted.
            refresh_token = None
    if connection:
        connection.account_email = None
        connection.encrypted_credentials = None
        connection.status = "disconnected"
        db.session.commit()
    if refresh_token:
        try:
            import requests
            requests.post("https://oauth2.googleapis.com/revoke", data={"token": refresh_token}, timeout=10)
        except Exception as exc:
            current_app.logger.warning("Gmail grant revocation failed (%s)", type(exc).__name__)
