# Google Gmail API OAuth guide

The portal currently sends mail through SMTP. Gmail API OAuth is a separate integration path; adding OAuth credentials alone will not switch the mail sender. This guide covers a designated Gmail/Google Workspace sender account used by server-side reminders and notifications. Students and Examiners do not individually authorize Gmail.

## Google Cloud setup

1. Create or select a Google Cloud project and enable the **Gmail API**.
2. Configure the Google Auth Platform consent screen. Use **Internal** for a Workspace-only organization when available; otherwise use **External** and follow Google's testing/publishing and verification requirements.
3. Add only the `https://www.googleapis.com/auth/gmail.send` scope. It is a sensitive scope that allows sending mail on behalf of the connected account. Avoid broad mailbox scopes such as `https://mail.google.com/`.
4. Create an OAuth client of type **Web application** for the Flask backend. Register the exact callback URI, for example `https://portal.example.edu/api/v1/admin/google-mail/callback`. Local development can use a separate client and callback such as `http://localhost:5000/api/v1/admin/google-mail/callback`.
5. Download the client configuration and store it outside source control. Do not place the JSON file under a public/static folder or commit it.

Google's [Gmail Python quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python) is useful for a local proof of concept. It uses an installed-app flow and explicitly describes itself as a simplified testing approach; a deployed Flask site should use the [OAuth web-server flow](https://developers.google.com/identity/protocols/oauth2/web-server) instead.

## Flask authorization and token handling

1. Install the backend requirements. The integration uses `google-api-python-client`, `google-auth`, `google-auth-oauthlib`, `requests`, and `cryptography`.
2. Add an Admin-only **Connect Google mail account** action. Start the OAuth flow on the server, generate a random `state`, keep it in the signed Flask session, and redirect the Admin to Google's consent URL. Request offline access so the backend can send scheduled mail while the Admin is not present.
3. In the callback, compare and consume the saved `state`, exchange the authorization code, and keep the refresh token. Never return tokens or client secrets to Vue or print them in logs.
4. Store the client secret in backend environment configuration and refresh/access tokens encrypted in the dedicated `gmail_connection` table. The encryption key must be a valid Fernet key, kept private and stable. The Admin disconnect action clears local stored credentials and requests grant revocation.
5. Build a Gmail API service in `backend/app/services/email.py` using `google.oauth2.credentials.Credentials` and `googleapiclient.discovery.build("gmail", "v1", credentials=credentials)`. Refresh expired access tokens with `google.auth.transport.requests.Request`, then persist the refreshed token state.
6. Build an RFC-compliant MIME message with `email.message.EmailMessage`, URL-safe-base64 encode its bytes, and call `service.users().messages().send(userId="me", body={"raw": encoded_message}).execute()`. Keep the existing `send_notice` boundary so email failures do not undo a booking or result publication. Log safe error types and message IDs only; never log bearer tokens or message bodies containing student data.
7. Select the mail provider with `EMAIL_PROVIDER=smtp|gmail_api`; keep SMTP as the default until Gmail sending has been tested. Run the existing notification tests with the mail sender mocked, then perform one controlled end-to-end send to an internal test mailbox before enabling scheduled reminders.

ExamWise account authentication uses email and password. Gmail OAuth in this guide is only for the server-side sender and does not authenticate students, examiners, or admins.

For OAuth clients, Google's server-side flow recommends validating `state`; offline access is needed for work performed when the user is absent. See Google's [web-server OAuth flow](https://developers.google.com/identity/protocols/oauth2/web-server) and [Gmail scope list](https://developers.google.com/workspace/gmail/api/auth/scopes). The Gmail API send method and accepted scope are documented at [users.messages.send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send).

## Current project status

The portal supports both SMTP and Gmail API sending. `EMAIL_PROVIDER=smtp` remains the default; choose `gmail_api` to route existing notification calls through Gmail. SMTP settings remain supported. The Admin connection endpoints and settings panel keep OAuth secrets and credentials out of browser responses. Add real values only to the untracked `backend/.env`; `.env.example` uses placeholders. The database table is added by the existing `python -m flask --app run.py init-data` command, which creates missing tables without dropping existing records. Automated tests mock Google API behavior; a real connection and recipient delivery still require a manual test using the configured Google test account.

Daily reminders send email and retain in-app notifications. Email delivery uses the selected provider, so configure SMTP or connect the Gmail sender before relying on reminders.

Google currently expires refresh tokens after seven days for an External OAuth app in Testing status when it requests Gmail scopes. The sender therefore may need to be reconnected periodically during testing; the integration shows a reconnect-required status after token refresh fails. See Google's [OAuth refresh token expiration guidance](https://developers.google.com/identity/protocols/oauth2#expiration).
