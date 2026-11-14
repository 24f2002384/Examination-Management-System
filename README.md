# Examination Management Portal (EMP)

EMP is a local academic examination workflow application. Flask provides a session-based JSON API, Vue 3 provides the browser interface, SQLite stores application data, Redis supports caching and Celery queues, and Celery workers run exports, reminders, monthly reports, and artifact cleanup.

## Quick start on Windows

Use Python 3.10+, Node.js 18+, and PowerShell. Redis supports caching and scheduled/background tasks. The Student's one-person CSV export works without Redis/Celery; monthly report generation and scheduled jobs still use a worker. Docker Desktop with Linux containers is one documented way to run Redis. See the complete setup in [Installation and Run Guide](docs/INSTALLATION_AND_RUN_GUIDE.md).

1. In PowerShell, change to the project folder, then open it in VS Code:

   ```powershell
   code .
   ```

2. In a backend terminal:

   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   Copy-Item .env.example .env
   ```

3. Edit `backend/.env`. Set a private `SECRET_KEY` and a unique `ADMIN_EMAIL` and `ADMIN_PASSWORD` before initializing the database.

   The tracked `.env.example` contains placeholders only. Do not copy its placeholder Admin credentials into a production database, and keep actual OAuth/client secrets only in the untracked `backend/.env`.

4. In a separate terminal, start Redis (if using Docker Desktop):

   ```powershell
   docker run --name emp-redis --rm -p 6379:6379 redis:7-alpine
   ```

5. In the backend terminal, initialize the database and start Flask:

   ```powershell
   python -m flask --app run.py init-data
   python -m flask --app run.py run --debug
   ```

6. In a frontend terminal:

   ```powershell
   cd frontend
   npm ci
   npm run dev
   ```

7. Open <http://localhost:5173>. The API base URL is <http://localhost:5000/api/v1>; health check: <http://localhost:5000/api/v1/health>.

8. Start a Celery worker and Celery Beat in separate backend terminals for monthly reports, reminders, and scheduled cleanup:

   ```powershell
   celery -A run:celery worker --loglevel=INFO --pool=solo
   ```

   ```powershell
   celery -A run:celery beat --loglevel=INFO
   ```

The Vue production build is created by `npm run build` in `frontend` and written to `frontend/dist`. `npm run preview` serves that build locally for review. This repository does not configure Flask or another web server to host `dist`, and a local build does not publish the application to the internet.

## Gmail API email delivery

SMTP remains the default and its existing settings and behavior are unchanged. To use the optional Gmail API sender:

1. From `backend`, install the updated dependencies with `pip install -r requirements.txt`.
2. Add these settings to the existing, untracked `backend/.env` without replacing other values:

   ```dotenv
   EMAIL_PROVIDER=smtp
   GOOGLE_CLIENT_ID=your-web-oauth-client-id
   GOOGLE_CLIENT_SECRET=your-web-oauth-client-secret
   GOOGLE_REDIRECT_URI=http://localhost:5000/api/v1/admin/google-mail/callback
   GOOGLE_TOKEN_ENCRYPTION_KEY=your-generated-fernet-key
   ```

   Generate a Fernet key in the active backend environment with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. Keep it private and stable; losing it prevents the app from decrypting the stored Gmail credentials.
3. Register the Gmail mail callback shown above on the OAuth Web application. Gmail OAuth is used only by the server-side email sender; ExamWise account authentication uses email and password.
4. From `backend`, run `python -m flask --app run.py init-data`. This creates the new Gmail connection table using the existing additive `create_all` setup and does not drop or reset existing records. Ensure `DATABASE_URL` points to the database this application should use.
5. Restart Flask, sign in as Admin, open **Settings → Gmail email delivery**, and choose **Connect Gmail**. Google returns to the Admin settings page; the connection status shows the verified account address.
6. To send one controlled test email, keep `EMAIL_PROVIDER=smtp` while connecting, then set `EMAIL_PROVIDER=gmail_api` and restart Flask. In a backend terminal run `python -m flask --app run.py shell`, then call `send_email("your-test-address@example.com", "EMP Gmail test", "This is a test message.")` after importing it with `from app.services.email import send_email`. The shell reports whether the send completed; check the recipient mailbox. No automated test contacts Google or sends email.
7. To switch back, set `EMAIL_PROVIDER=smtp` and restart Flask. SMTP configuration remains available and unchanged.

The OAuth client secret and Fernet encryption key are read only by Flask. Access and refresh credentials are encrypted in a dedicated database table; only connection status and account address are returned to Vue. Disconnect from the same Admin settings section to clear local credentials and request Google grant revocation. The local Gmail flow requires the Admin session to remain available through Google's browser redirect. Google test-mode access is limited to configured test users.

Daily reminders now send email and retain the in-app notification. Configure SMTP or connect the Gmail sender and select `EMAIL_PROVIDER=gmail_api` before relying on email delivery. `REMINDER_DELIVERY=email` is the default and is set in the local example configuration.

## Documentation

- [Installation and Run Guide](docs/INSTALLATION_AND_RUN_GUIDE.md) — Windows prerequisites, terminals, startup, first login, production build.
- [Manual Update Guide](docs/MANUAL_UPDATE_GUIDE.md) — actual Admin, Examiner, and Student UI operations plus code/configuration changes.
- [Project Structure](docs/PROJECT_STRUCTURE.md) — source folders and important files.
- [Troubleshooting](docs/TROUBLESHOOTING.md) — project-specific diagnostics and safe remedies.
- [Google Gmail OAuth Guide](docs/GOOGLE_GMAIL_OAUTH_GUIDE.md) — official Gmail API OAuth setup guidance and current integration boundary.
- [Project Status](docs/PROJECT_STATUS.md) — implemented workflows, gaps, current verification evidence, and known limits.

## Current implementation

The login page requires choosing Admin, Examiner, or Student; the server checks that the choice matches the account. Public registration creates Students only; Admin creates Examiner accounts. Admin manages courses, examinations and their lifecycle, rubrics, Examiner accounts, Student activation, slots, bookings, submitted evaluations, reports, audit history, profile, and sign-in credentials. In-app notifications are available, and booking/cancellation/result emails are sent when SMTP is configured. Students can request/download their history CSV from the Dashboard without running Celery. Run `python -m flask --app run.py seed-demo` from `backend` to add a small idempotent demo course/exam/rubric catalogue without creating or modifying user accounts. See the guides for details.

Students can attach a PDF, DOC, DOCX, or TXT work document to an active booking (10 MB maximum); Examiners can download it from the evaluation screen. Uploads are stored privately under `backend/instance/submissions`. ExamWise account authentication uses email and password. Examiner slot tables show local 12-hour times, capacity used, remaining seats, and status. Examination availability, booking/work/evaluation updates, published results, and scheduled reminders use the configured email sender and retain in-app notifications.

Initial database setup is `python -m flask --app run.py init-data` from `backend`. It creates tables, applies the repository's additive SQLite compatibility updates, and ensures the configured Admin exists. This is not a general migration framework; back up SQLite before schema changes. Never delete the database to resolve an error.

## Checks recorded for the current changes

- `python -m pytest -q` from `backend` — passed, 24 tests, on 2026-11-13, covering role checks, email notification hooks, demo seed idempotence, exports, and live Student examination/slot visibility.
- `python -m compileall -q app tests config.py run.py` from `backend` — passed on 2026-11-13.
- `npm run build` from `frontend` — passed on 2026-11-13; Vite transformed 45 modules.
- `python -m flask --app run.py seed-demo` from `backend` — added 3 demo courses, 4 draft exams, 12 rubric criteria after a SQLite backup; rerun confirmed idempotence with zero additional records.
- `python -m pytest -q` from `backend` — passed, 23 tests, on 2026-11-11 during the earlier Admin recovery work. This suite was not rerun for the current dashboard/routing changes.
- `python -m compileall -q app tests config.py run.py` from `backend` — passed on 2026-11-11 during the earlier Admin recovery work.
- `npm run build` from `frontend` — passed with 43 modules on 2026-11-11 during the earlier Admin recovery work.
- `python -m flask --app run.py init-data` from `backend` with `DATABASE_URL=sqlite://` — passed using an in-memory database.
- `python -m flask --app run.py routes` from `backend` — passed; routes including `/api/v1/health` were registered.
- `python -m flask --app run.py reset-admin --help` from `backend` — passed; local Admin recovery command is registered.
- Empty `EXPORT_DIR`/`REPORT_DIR` configuration check — passed; both resolve to private folders under `backend/instance`.
- Live Redis, Celery worker/Beat, SMTP delivery, and a browser end-to-end session were not exercised. The automated task tests use eager Celery behavior and a fake cache client.

See [Project Status](docs/PROJECT_STATUS.md) for outstanding limitations and check scope.
