# Manual Update Guide

This guide describes actions available in the current UI and safe ways to change source/configuration. Website changes use authenticated API requests with CSRF protection and server-side validation. Editing SQLite rows directly bypasses those checks and should be limited to careful recovery/inspection.

## Admin workflows

Sign in as Admin and use the left navigation. List pages include search, status filters where applicable, pagination, and **View**. Most failed validation is shown in a notification; correct the indicated fields and retry.

### Courses — **Courses** (`/admin/courses`)

- Select **Create Course**. Enter **Course code**, **Course name**, and optional **Description**, then save. Code and name are required; course codes are unique; an active course is required to create an examination.
- Use **Edit** to update supported course fields. Select **Archive** for an active course to set status archived; archived courses cannot be selected for new exams. **Remove** is available only for a course with no examinations; courses with examination history must be archived.

### Examinations, dates, and Examiner assignment — **Examinations** (`/admin/examinations`)

- Select **Create Examination**. Required: active **Course**, **Name**, **Type** (`Viva`, `Practical`, `Project Demo`, `Assessment`), **Duration (minutes)**, and **Maximum marks**. Optional: description, active assigned Examiner, **Slot creation opens/closes**, and **Booking opens/closes**. Each date field has **Now** and custom-minute minus/plus controls. The default adjustment is 15 minutes; change the number before using +/- to choose another interval.
- New exams use the shared default instructions configured by `DEFAULT_EXAM_INSTRUCTIONS` in `backend/.env`. Select **Use shared default instructions** to insert them into the field; enter exam-specific text to override them. Leaving the instructions empty uses the shared default.
- Dates use the browser's local `datetime-local` inputs and are sent as ISO timestamps. When entered, each start must be before its matching end; windows must be chronologically sensible under server validation. An assigned Examiner must be active.
- Use **Edit** to change supported fields; the backend prevents unsafe lifecycle/date changes once downstream activity exists. Assign or reassign the examination's Examiner by editing **Assigned examiner**.
- Use the lifecycle dropdown in the row to move through valid states: `draft` → `slot_creation` → `booking_open` → `closed` → `completed`. The API rejects invalid transitions. Students can browse/book only when the exam is in an eligible state and the booking window is open.

### Rubric criteria — **Rubrics** (`/admin/rubrics`)

- Select the examination, enter **Criterion name**, optional description, **Max marks** (>0), and **Weight %** (0–100), then select **Add criterion**.
- Select **Edit** to update name, maximum marks, or weightage via prompts; select **Delete** to remove a criterion. Mark totals and weight percentage totals are displayed. Evaluations use the current rubric; do not alter a rubric after assessment work without considering existing scores and validation rules.

### Users — **Examiners** and **Students**

- **Examiners**: select **Create Examiner**. Full name, valid unique email, department, and optional initial password are accepted. If password is omitted, the app generates and displays a one-time initial password; record it securely and share it with the intended Examiner. Use **Edit** for name/email/department and **Deactivate/Activate** for account access. Email is unique.
- **Students**: students register themselves at `/register`; Admin cannot create them from this UI. Under **Students**, use **View** and **Deactivate/Activate**. **Delete** permanently removes that student, their profile, bookings, evaluations, submissions, notifications, and private exports; booked seats are released. Audit history is retained with the deleted actor reference cleared. Confirm the irreversible action in the prompt. Admin cannot edit student profile fields from this screen. Students edit their own profile.
- Admin email/password are changed from **Settings** (`/admin/settings`) in **Admin sign-in details**. Enter the new email, current password, and optionally a new password plus matching confirmation. New passwords must be 10–128 characters; email must be valid and unique. The current password is required for either credential change. Updating credentials does not send an email verification message.
- If the Admin cannot sign in, set `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `backend/.env`, back up the database, then run `python -m flask --app run.py reset-admin` from `backend`. This resets the first Admin account by ID (or seeds one if none exists); it does not reset every Admin. It refuses the built-in placeholder credentials and refuses an email already used by another account. Keep the terminal private because the new email is printed, and never put the password on the command line.

### Slots — **Slots** (`/admin/slots`)

- The Admin page lists slots, shows capacity/occupancy, and permits changing **Capacity** inline. The server rejects a capacity below the number of reserved seats.
- Select **View** on a slot, then use **Reschedule slot** or **Reassign examiner**. These are exceptional actions with confirmation prompts. Rescheduling asks for ISO start/end times, requires start before end, and keeps existing bookings attached. Reassignment asks for an active Examiner's numeric user ID; attached evaluations and booked Students are updated/notified.
- Slot creation is implemented in the Examiner portal rather than an Admin create form. Examiner slots can be created only during the configured slot-creation window. The **My Slots** table displays only examination, date, 12-hour time, capacity used, remaining seats, status, meeting link, and actions.

### Bookings — **Bookings** (`/admin/bookings`)

- The list allows status filtering and a status dropdown for a row. Server rules guard valid status changes and update capacity reservations.
- Select **View**, then **Move booking to another slot**. Enter the target slot ID for the same examination. The target must be valid and have capacity; duplicate active bookings for the same Student and exam are blocked. The change updates reservations transactionally.
- Admin can reschedule/reassign slots as described above. There is no separate Admin booking-creation button.

### Evaluate review and publication — **Evaluations** (`/admin/evaluations`)

- Review submitted evaluations. Select **Publish result** and confirm. Only submitted evaluations can be published. A published result becomes visible to the Student through **Results**; unpublished marks are withheld from Student result endpoints and exports.

### Reports — **Reports** (`/admin/reports`)

- Review summary totals/workload and set the available date filters for the summary.
- **Generate previous month** queues the monthly HTML report. Check the history row and job status; use **Download HTML** when an artifact is ready. Email delivery is optional and requires SMTP configuration. The scheduled report also requires Celery Beat.
- Student history CSV is requested from the Student **Dashboard**, not the Admin Reports page. Admin Reports contains summary statistics and monthly HTML reports.

### Settings and activity — **Settings**, **Activity history**

- **Settings** shows the Admin profile name and read-only application name/timezone. The profile form saves the display name. Under **Admin sign-in details**, update the email and/or password after confirming the current password. The global **Profile** link also requires the current password when changing the email. Neither screen edits application-wide environment configuration.
- **Activity history** (`/admin/audit-log`) shows recorded audit entries. Not every read or every possible system event is logged.

## Examiner workflows

Sign in using the account created by Admin.

- **My Examinations** (`/examiner/my-examinations`): search assigned examinations, select **Details** to review instructions, timeline, rubric, slot counts, and booked students. The examiner sees only their assignments.
- **My Slots** (`/examiner/my-slots`): choose an assigned examination, start and end date/time, capacity (1–100), and optional meeting link, then select **Create slot**. Use **Now** to fill the current local time and +/- to move by a custom number of minutes (15 by default). Server checks assignment, date validity, capacity, examination lifecycle, and the configured slot-creation window. The table uses 12-hour AM/PM times and separates capacity used, seats left, and slot status. Use **Capacity** to change capacity or **Cancel slot** to cancel only when there are no active bookings.
- **Booked Students** or **Evaluations**: select **Evaluate** to view any attached student work document before scoring. For each rubric criterion enter marks from 0 to its maximum and optional remarks. **Save draft** keeps a draft editable; **Submit evaluation** validates required scores and locks it for review. Submitted evaluations cannot be edited by the Examiner in the normal UI. Admin publishes the result.
- Examiner can update name, email, and department under **Profile**. Email must be valid and unique.

## Student workflows

- **Dashboard** (`/dashboard`): the **Examination updates** panel shows exams whose slots are being prepared, whose booking window is scheduled to open, or whose booking window is open now. This panel refreshes every 30 seconds and when the browser tab regains focus. Use **Refresh** for an immediate update. An exam in `slot_creation` is not bookable yet; use **Browse examinations** after the booking window opens.
- **Register and sign in**: select **Create your account** on the login page. Registration creates a Student only and sends a welcome email through the configured email provider when delivery is available. Full name, valid email, and password of at least 10 characters are required; email must be unique. All roles sign in with ExamWise email and password; Examiners are created by Admin.
- **Profile**: update full name, email, department, program, and optional student number. Email must be valid and unique; student number, when provided, must be unique.
- **Browse Examinations**: search by exam/course and filter by course/type. Select **Details** to review the description, date/eligibility, available slots, seats, duration, and marks. **Book slot** is offered only when eligible. The server checks booking window, seat availability, account eligibility, and prevents multiple active bookings for the same examination.
- **My Bookings**: inspect status and select **Submit work** to upload a PDF, DOC, DOCX, or TXT report/answer document (10 MB maximum). Project demos, assessments, viva, and practicals use this same upload; no question bank is required. You may replace your document until the Examiner submits evaluation. Select **Cancel** for an active booking; cancellation is permitted only inside the configured deadline/window, and the seat becomes available. **Examination History** shows non-active bookings.
- **Results**: select **Scorecard** after Admin publication. Submitted-but-unpublished marks remain hidden.
- Student history CSV export is available on the **Dashboard**. Select **Request export**; generation completes in the request and the job appears as completed or failed. Select **Download CSV** when ready. The file contains this Student's bookings and only published marks. This single-Student export does not require Redis or a Celery worker. If it fails, retry once and inspect the Flask terminal for `Student export failed`; do not share session cookies, passwords, or tokens when sharing logs.

Opening the site root (`/`) sends signed-out users through login and signed-in users to their dashboard.

## Demo catalogue, roles, and email notifications

- From `backend`, run `python -m flask --app run.py seed-demo` to add sample courses, draft examinations, and rubrics. The command is safe to rerun, skips matching sample records, and does not create or modify user accounts. Assign an Examiner and review the dates/status before using sample exams.
- The sign-in page has separate **Admin**, **Examiner**, and **Student** choices. The selected role must match the account's stored role. Public registration always creates Students; Admin creates Examiner accounts.
- In-app notifications remain available alongside event emails for examination availability/updates, bookings, cancellations, submissions, evaluation completion, published results, and reminders. Configure SMTP or connect Gmail API and choose `EMAIL_PROVIDER` in `backend/.env`. Daily reminder mail is enabled by `REMINDER_DELIVERY=email` and requires Celery Beat to be running.
- Gmail API OAuth is only for the server-side email sender and is separate from ExamWise account authentication. See [Google Gmail OAuth Guide](GOOGLE_GMAIL_OAUTH_GUIDE.md) for mail-provider setup.

## Safe source and configuration changes

Make a backup of the database before schema/data work (see [Database safety](#database-safety)). Keep secrets in ignored local `.env` files. `sources/` in this project is synced, read-only reference material—do not edit it.

| Change | Actual files | Steps and verification |
|---|---|---|
| Flask settings, DB URL, Redis, email, job schedules | `backend/.env`, `backend/.env.example`, `backend/config.py`, `backend/app/celery_app.py` | Change local values in `.env`; keep the example secret-free. Restart Flask, Celery worker, and Beat after configuration changes. Check their startup logs and `/api/v1/health`. Change Beat schedule code in `celery_app.py`, then restart Beat. |
| Frontend layout, navigation, wording, styles | `frontend/src/views/`, `frontend/src/components/AppShell.vue`, `frontend/src/router/index.js`, `frontend/src/style.css`, `frontend/src/admin.css` | Edit the relevant Vue/CSS file. Vite normally hot reloads; inspect the Vite terminal and browser console. Run `npm run build` from `frontend` to catch compilation/import errors. |
| Backend route, validation, business rules | `backend/app/api/routes.py`, `admin_routes.py`, `portal_routes.py`, `jobs_routes.py` | Edit the endpoint and its validation/authorization. Flask debug mode reloads many Python edits; restart Flask if it does not. Run relevant backend tests from `backend`. Keep role, ownership, CSRF, and transaction checks server-side. |
| Add/change an API endpoint | `backend/app/api/*.py`, `backend/app/api/__init__.py`, `frontend/src/api.js`, relevant Vue page | Implement and register the Flask route, then call it from Vue's shared `api.request`. Check `python -m flask --app run.py routes`, exercise the screen/API with an authorized account, then run backend tests and frontend build. |
| Database model/schema | `backend/app/models.py`, `backend/app/__init__.py` | Update the model and a safe additive SQLite schema update. Back up first. `db.create_all()` creates absent tables but does not alter existing tables; this app's `upgrade_sqlite_schema()` only applies listed additive compatibility changes. There is no Alembic migration system. Test against a copy of existing data before relying on the change. |
| Dependencies | `backend/requirements.txt`, `frontend/package.json`, `frontend/package-lock.json` | Backend: activate `.venv`, update the intended requirement, then `pip install -r requirements.txt`. Frontend: from `frontend`, use `npm install <package>` or edit package metadata and run `npm install`; commit both package and lock changes. Verify backend tests and `npm run build`. |
| Cache behavior/invalidation | `backend/app/services/cache.py`, cache decorators and mutation routes | Change TTL/key/namespace invalidation with care. Restart Flask. Redis cache errors are logged and reads fall back to SQLite. Verify relevant reads after a mutation; use a short TTL or explicit invalidation rather than deleting database data to address stale cached reads. |
| Celery jobs/retries | `backend/app/tasks.py`, `backend/app/celery_app.py`, `backend/config.py` | Edit task logic in `tasks.py`; schedules are registered in `celery_app.py`. Restart workers after task changes and Beat after schedule changes. Watch both logs, and test behavior using the existing task tests. |
| Initial Admin settings | `backend/.env`, `backend/app/services/auth.py`, `backend/app/__init__.py` | Set secrets before first `init-data`. `init-data` does not rotate an existing password. Change credentials while signed in under Admin **Settings**, or back up the database, update `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `.env`, then run `python -m flask --app run.py reset-admin` from `backend` for local recovery. Do not change password hashes by editing a DB row manually. |

### Restart guide

- Python route/model/service changes: Flask debug mode usually reloads Flask; restart the worker for task code, and restart Beat for schedule/config changes.
- Vue `.vue`, `.js`, or CSS changes: Vite hot reloads. Restart Vite after changing `VITE_API_URL` or dependency configuration.
- `.env` changes: restart Flask, worker, Beat, and/or Vite depending on which process reads the changed variable. Redis itself only needs restarting if its own server configuration changes.
- Dependency changes: stop the affected process, install dependencies, restart it.

When an error occurs, read the terminal that owns that process first. Flask tracebacks identify route/database problems, Vite output reports compile/import issues, and Celery output reports broker/task failures. The browser developer console and Network panel show frontend errors, API URL, and response status. Do not expose passwords, CSRF tokens, or session cookies when sharing logs.

## Database safety

### Location and initialization

The default URI is `sqlite:///emp.db`. Flask-SQLAlchemy resolves this relative path under the Flask instance directory, so the default database is `backend/instance/emp.db`. `init-data` runs SQLite compatibility additions, `db.create_all()`, the additions again, and idempotent Admin seeding.

There is no migration tool. `db.create_all()` does not change existing tables. The custom `upgrade_sqlite_schema()` uses explicit `ALTER TABLE ADD COLUMN`, data backfills, and index creation for known historical schemas; it is not a general versioned migration system. For schema changes: stop Flask and Celery processes, make a backup, test an additive update against a copy, preserve all existing data, then run `init-data` and verify with tests/health check. Avoid rebuilding tables or deleting the live DB without a deliberate data migration plan.

### Back up and restore the default database

Stop Flask, worker, and Beat first. From `backend`, create a consistent backup using Python's SQLite backup API:

```powershell
New-Item -ItemType Directory -Force .\backups | Out-Null
@'
import sqlite3
with sqlite3.connect("file:instance/emp.db?mode=ro", uri=True) as connection:
for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
print(name)
'@ | python -
```

This assumes the default database already exists. Confirm that `backups/emp-backup.db` was created and is non-empty. Keep backups outside source control and protect them as personal data.

To restore, stop all processes that use the DB, first rename/copy the current DB to a separate recovery file, then copy the chosen backup into place:

```powershell
Copy-Item .\instance\emp.db .\backups\emp-before-restore.db
Copy-Item .\backups\emp-backup.db .\instance\emp.db -Force
```

Start Flask and check `/api/v1/health`; confirm expected records before resuming work. Do not overwrite the only copy of the database.

If `DATABASE_URL` is changed, identify and back up that configured database path instead; the commands above specifically match the default SQLite location.

### Inspect safely and preserve constraints

For an inspection-only query, use Python's SQLite module from `backend` (example lists table names without changing records):

```powershell
@'
import sqlite3
with sqlite3.connect("file:instance/emp.db?mode=ro", uri=True) as connection:
    for (name,) in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        print(name)
'@ | python -
```

Use the website/API for normal updates. Direct database writes bypass validation and audit logging. Relationships rely on foreign keys; unique constraints protect emails, student numbers, one evaluation per booking, and duplicate bookings. Partial uniqueness prevents a Student from having more than one active booking per examination. Slot capacity relies on an atomic reservation update and must not be changed by editing `reserved_count`. Transactions group booking/reschedule changes; partial manual edits can corrupt counts or leave inconsistent records. If direct recovery is unavoidable, back up first, stop the app, use a transaction, check foreign-key integrity, and verify all related records before restarting.

## Production build after changes

From `frontend`:

```powershell
npm run build
```

Review Vite output for missing imports and compilation errors. Output goes to `frontend/dist`. The project has no configured static hosting or public deployment process; configure and secure those separately.
