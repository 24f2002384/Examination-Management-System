"""Admin-only endpoints for connecting the server's Gmail sender account."""
import secrets
import traceback
from urllib.parse import urlencode

from flask import current_app, jsonify, redirect, request, session

from ..services import gmail_oauth
from . import api
from .routes import error, login_required

admin = login_required("admin")
STATE_SESSION_KEY = "google_mail_oauth_state"
CODE_VERIFIER_SESSION_KEY = "google_mail_oauth_code_verifier"


def _settings_url(result):
    origin = current_app.config.get("FRONTEND_ORIGIN", "http://localhost:5173").rstrip("/")
    return f"{origin}/admin/settings?{urlencode({'gmail': result})}"


def _safe_error(exc):
    return str(exc).replace("\n", " ")[:240] or "Gmail OAuth failed"


@api.get("/admin/google-mail/status")
@admin
def gmail_status():
    try:
        return jsonify(gmail_oauth.connection_status())
    except Exception as exc:
        current_app.logger.warning("Gmail status check failed (%s)", type(exc).__name__)
        return error("Could not read Gmail connection status", 500)


@api.get("/admin/google-mail/connect")
@admin
def gmail_connect():
    if not gmail_oauth.oauth_is_configured():
        return redirect(_settings_url("setup"))
    state = secrets.token_urlsafe(32)
    session[STATE_SESSION_KEY] = state
    try:
        result = gmail_oauth.authorization_url(state)
        url, code_verifier = result if isinstance(result, tuple) else (result, None)
        if code_verifier:
            session[CODE_VERIFIER_SESSION_KEY] = code_verifier
        return redirect(url)
    except Exception as exc:
        session.pop(STATE_SESSION_KEY, None)
        session.pop(CODE_VERIFIER_SESSION_KEY, None)
        current_app.logger.warning("Gmail authorization could not start (%s)", type(exc).__name__)
        return redirect(_settings_url("error"))


@api.get("/admin/google-mail/callback")
@admin
def gmail_callback():
    expected_state = session.pop(STATE_SESSION_KEY, None)
    code_verifier = session.pop(CODE_VERIFIER_SESSION_KEY, None)
    returned_state = request.args.get("state")
    if not expected_state or not returned_state or not secrets.compare_digest(expected_state, returned_state):
        return redirect(_settings_url("error"))
    if request.args.get("error"):
        return redirect(_settings_url("cancelled"))
    if not request.args.get("code"):
        return redirect(_settings_url("error"))
    try:
        gmail_oauth.complete_authorization(request.url, returned_state, code_verifier)
    except Exception as exc:
        current_app.logger.warning("Gmail authorization callback failed (%s): %s", type(exc).__name__, _safe_error(exc))
        if current_app.debug:
            current_app.logger.debug(traceback.format_exc())
        result = "scope_missing" if "permission" in str(exc).lower() else "error"
        return redirect(_settings_url(result))
    return redirect(_settings_url("connected"))


@api.post("/admin/google-mail/disconnect")
@admin
def gmail_disconnect():
    try:
        gmail_oauth.disconnect_connection()
        return jsonify({"connected": False, "status": "disconnected", "email": None})
    except Exception as exc:
        current_app.logger.warning("Gmail disconnect failed (%s)", type(exc).__name__)
        return error("Could not disconnect Gmail", 500)
