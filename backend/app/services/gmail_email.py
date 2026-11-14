"""Gmail API implementation of the shared email sender interface."""
import base64
from email.message import EmailMessage

from flask import current_app

from .gmail_oauth import (GmailOAuthError, load_credentials, mark_reauthorization_required,
                          persist_refreshed_credentials)


class GmailDeliveryError(Exception):
    """Safe delivery error; provider details and credentials are intentionally omitted."""


def send_gmail_email(to, subject, body, html_body=None):
    if not to:
        return {"delivered": False, "provider": "gmail_api", "reason": "Recipient is missing"}
    try:
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        credentials = load_credentials()
        if not credentials.valid:
            if not credentials.expired or not credentials.refresh_token:
                raise GmailOAuthError("Gmail credentials need to be reconnected")
            try:
                credentials.refresh(Request())
            except Exception as exc:
                try:
                    mark_reauthorization_required()
                except Exception as status_exc:
                    current_app.logger.warning("Gmail reconnect status update failed (%s)", type(status_exc).__name__)
                current_app.logger.warning("Gmail credential refresh failed (%s)", type(exc).__name__)
                raise GmailDeliveryError("Gmail authorization needs to be reconnected") from None
            persist_refreshed_credentials(credentials)

        message = EmailMessage()
        message["To"] = to
        message["Subject"] = subject
        message.set_content(body)
        if html_body:
            message.add_alternative(html_body, subtype="html")
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        return {"delivered": True, "provider": "gmail_api", "message_id": result.get("id") if isinstance(result, dict) else None}
    except GmailDeliveryError:
        raise
    except GmailOAuthError as exc:
        raise GmailDeliveryError(str(exc)) from None
    except Exception as exc:
        status = getattr(getattr(exc, "resp", None), "status", None)
        if status == 401:
            try:
                mark_reauthorization_required()
            except Exception as status_exc:
                current_app.logger.warning("Gmail reconnect status update failed (%s)", type(status_exc).__name__)
            raise GmailDeliveryError("Gmail authorization needs to be reconnected") from None
        if status == 403:
            raise GmailDeliveryError("Gmail API rejected sending; verify the gmail.send permission") from None
        if status == 400:
            raise GmailDeliveryError("Gmail API rejected the message request") from None
        current_app.logger.warning("Gmail API delivery failed (%s)", type(exc).__name__)
        raise GmailDeliveryError("Gmail could not send this message") from None
