# Project Status

**Scope:** Current Examination Management Portal implementation (M1–M4 as represented in the workspace). This document records the code present and checks actually executed during the documentation and maintenance pass. It is not a claim that every feature has been manually exercised in a browser.

## Working features in the current source

- Session login/logout, CSRF-protected mutations, Student registration, role authorization, profile workflows, and explicit sign-out controls in the dashboard header/sidebar.
- Admin course management/archive; examination create/edit and lifecycle transitions; scheduling/booking windows; Examiner create/edit/activate; Student activation; rubric management; slot capacity, reschedule/reassign; booking review/status/reschedule; submitted evaluation review and result publication; report summary, monthly report history/download, Admin profile/settings, and audit history.
- Admin can update sign-in email/password from **Settings → Admin sign-in details** after confirming the current password. The local `reset-admin` Flask command provides recovery using private `.env` values when the Admin cannot sign in.
- Examiner assigned-examination and slot workflows, booked-student lists, rubric-based draft/submitted evaluations, and private downloads of student work.
- Student examination discovery/search/filters, slot booking/cancellation, PDF/DOC/DOCX/TXT work submission, booking/history views, profile edits, and published scorecards.
- Student dashboard lists exams being prepared, scheduled to open, or currently open; it refreshes the exam updates periodically. The `/` site root routes signed-out users to login and signed-in users to their dashboard.
- Password login requires selecting the matching Admin, Examiner, or Student role; Students and Examiners can also sign in with Google when an existing account matches their verified Google email. Public registration creates Student accounts only. Admin creates Examiner accounts.
- Student CSV export is available from the Dashboard and runs without Redis/Celery. A CLI demo seeder adds sample courses, draft exams, and rubrics without altering user accounts.
- Email notices cover examination availability/updates, bookings, cancellations, student submissions, completed evaluations, published results, and scheduled reminders. SMTP remains supported and Gmail API is selectable; delivery requires valid provider configuration. In-app notifications remain available.
- Student examination browse/detail endpoints read fresh records; a regression check verifies a current booking window and its future slot are both visible.
- Admin exam dates and Examiner slot times have Now and custom-minute adjustment controls. Exams inherit shared `DEFAULT_EXAM_INSTRUCTIONS`; Admin can replace them with exam-specific instructions.
- Examiner slot tables show explicit capacity, seats remaining, status, and 12-hour AM/PM times rather than rendering backend fields through generic date detection.
- Gmail mail sender OAuth uses server-side encrypted tokens; Google login uses an independent minimal identity scope and requires its own authorized callback URL in Google Cloud.
- Examination lifecycle timestamps are normalized to UTC for storage/API output and converted to browser local time for admin editing, avoiding time shifts on edit.
- Redis-backed selected JSON reads with cache invalidation and SQLite fallback on cache-read errors.
- Celery task implementations for reminders, monthly reports, Student CSV exports, and expired artifact cleanup; Beat schedule definitions are present.
- SQLite models, seed-admin and init-data commands, additive compatibility updates for known older schemas, and tests covering API, roles, Admin operations, and task behavior.

## What can be managed in the website

- Admin can manage the catalogue, examination timeline/status, rubrics, Examiner accounts and Student activation, schedules/bookings, result publication, reports, audit history, and sign-in credentials.
- Examiners manage assigned slots and evaluations.
- Students self-register, maintain their profile, browse and book/cancel eligible slots, view booking/history, and view published results.

Current gaps: Admin has no Student creation/profile-edit UI or Admin slot-creation form; settings do not edit application-wide configuration. Examiners cannot change the overall examination lifecycle status; Admin retains that control. There is no email-based password reset or email verification. There is no production hosting/deployment configuration.

## Manual setup required

- Install Python and Node.js; install/start Redis if using cache or background work.
- Create `backend/.env`, set a private `SECRET_KEY` and unique Admin email/password before first initialization.
- Initialize via `python -m flask --app run.py init-data` from `backend`.
- Start Flask and Vite in separate terminals. Start Celery worker for monthly reports and Beat for scheduled reminders/reports/cleanup; Student CSV exports work without them.
- Configure SMTP variables only if actual email delivery is required.

The local default Admin credentials are deliberately not supplied here. The Admin creation values come from local environment settings and apply only when no Admin exists. The first Admin's existing password is not changed by rerunning init-data; use the Settings form or the documented `reset-admin` recovery command to change credentials.

## Verification evidence

The older Admin recovery verification below was run on 2026-11-11; dashboard/root-route changes were checked on 2026-11-12. Role, mail, export, seed data, and UI changes were checked on 2026-11-13.

| Command and working directory | Result |
|---|---|
| `python -m pytest -q` (`backend`) | Passed — 23 tests in 15.20s, including reset followed by login and an assertion that the Examiner record remains unchanged. |
| `python -m compileall -q app tests config.py run.py` (`backend`) | Passed — no syntax/bytecode compilation errors. |
| `npm run build` (`frontend`) | Passed — Vite 5.4.21 transformed 43 modules and built `dist`, including the new sign-in settings and sign-out buttons. |
| `$env:DATABASE_URL='sqlite://'; python -m flask --app run.py init-data` (`backend`) | Passed — initialized an in-memory SQLite database and ensured Admin without creating/changing the persistent default database. |
| `python -m flask --app run.py routes` (`backend`) | Passed — Flask registered the API routes, including `/api/v1/health`, role portals, Admin operations, exports, reports, and notifications. |
| `python -m flask --app run.py reset-admin --help` (`backend`) | Passed — recovery command and help text registered. |
| Python config check with empty `EXPORT_DIR` and `REPORT_DIR` (`backend`) | Passed — empty values now resolve to `backend/instance/exports` and `backend/instance/reports`. |
| `python -m compileall -q app` (`backend`, 2026-11-12) | Passed — no syntax/bytecode compilation errors in backend app modules. |
| `npm run build` (`frontend`, 2026-11-12) | Passed — Vite 5.4.21 transformed 44 modules and built `dist` after adding Student examination updates and root routing. |
| `python -m flask --app run.py seed-demo` (`backend`, 2026-11-13) | Passed — added 3 sample courses, 4 draft examinations, and 12 rubric criteria after making a local database backup. No user accounts were created or changed. |
| `python -m flask --app run.py seed-demo` (`backend`, 2026-11-13, repeated) | Passed — added zero additional records; seeding is idempotent. |
| `python -m pytest -q` (`backend`, 2026-11-13) | Passed — 24 tests in 22.23s, including selected-role login, registration role enforcement, email booking notice hooks, demo seeding, Student CSV export without Celery, and current Student exam/slot visibility. |
| `python -m compileall -q app tests config.py run.py` (`backend`, 2026-11-13) | Passed — no syntax/bytecode compilation errors. |
| `npm run build` (`frontend`, 2026-11-13) | Passed — Vite 5.4.21 transformed 45 modules after date-time controls, shared instructions, readability, and typography changes. |
| `python -m pytest -q` (`backend`, 2026-11-13, final run) | Passed — 24 tests in 22.23s after replacing an old cache assertion; Student browse/detail reads are deliberately uncached. |

The 2026-11-11 checks verify earlier Admin recovery work. The 2026-11-12 checks verify backend syntax and frontend production compilation after the dashboard/root-route change. The 2026-11-13 checks verify the current tests, syntax, production build, and sample seeding. They do not prove a real browser interaction or live SMTP/Gmail delivery.

## Pending checks and limitations

- Live Redis server, actual asynchronous Celery queue/worker/Beat scheduling, and SMTP delivery were not run in this environment. Unit/task tests use fake cache behavior or Celery eager execution.
- Full browser/API end-to-end workflows across Admin, Examiner, and Student were not executed in this verification pass.
- Live browser sign-out behavior was not manually exercised; the button uses the existing tested `/api/v1/auth/logout` endpoint.
- SMTP delivery still needs real credentials and a live SMTP server; current tests verify notification hooks without sending mail externally.
- Gmail API OAuth, token persistence, and provider switching are guidance only and remain unimplemented.
- There is no Alembic or other general versioned migration framework. The custom updater handles only explicitly listed additive changes; back up data and review custom migrations for new schema changes.
- No production static hosting, reverse proxy, HTTPS, deployment or production process manager is configured.
- No email-based password reset or email verification is implemented. Existing Admin credentials are not rotated by `init-data`; credential updates require the current password in the UI or local access to run the recovery command.
- SMTP is optional and will not send unless configured. Local reminders can use in-app notifications.

## Next milestone boundary

Do not treat these documentation/verification updates as implementation of the next product milestone. The next milestone has not been started here.
