# Troubleshooting

Use the terminal that owns the failing process first. Avoid posting `.env`, passwords, session cookies, CSRF tokens, or personal export data in logs/screenshots. Back up SQLite before any database recovery attempt.

## `python` or `pip` is not recognized

**Symptom:** PowerShell cannot find the Python command. **Likely cause:** Python is missing or not on PATH. **Check:** `python --version`; if that fails try `py -3 --version`. **Fix:** install Python 3.10+ and enable PATH, reopen PowerShell, or use `py -3` in setup commands. Prefer `python -m pip ...` or `py -3 -m pip ...` so pip matches that Python interpreter.

## Node.js or npm is not recognized

**Symptom:** `node` or `npm` is missing. **Check:** `node --version` and `npm --version`. **Fix:** install Node.js 18+ (npm is included), then close and reopen PowerShell. Do not install frontend packages using Python tooling.

## Backend dependency installation fails

**Symptom:** `pip install -r requirements.txt` reports build/download errors. **Check:** activate `backend/.venv`, verify Python version/network access, and update pip with `python -m pip install --upgrade pip`. Retry from `backend` with `pip install -r requirements.txt`. If one package fails, retain its full error for diagnosis; do not silently remove it from `requirements.txt` because it may be required by the API/jobs.

## Frontend install or build fails

**Symptom:** `npm ci` or `npm run build` exits nonzero. **Check:** run from `frontend`, use Node 18+, and read the first Vite/npm error for the missing package/import. `npm ci` requires the committed package lock to match package metadata. If dependencies were intentionally edited, run `npm install` to update the lockfile, then rerun `npm ci` and `npm run build`. Do not edit generated `dist` as the source of a fix.

## Flask cannot start or port 5000 is in use

**Symptom:** Flask errors on startup or says address already in use. **Check:** confirm `.venv` is active, the shell is in `backend`, `.env` exists, and inspect the Flask terminal traceback. Check port use with:

```powershell
Get-NetTCPConnection -LocalPort 5000 -State Listen
```

Stop the known process using the port, or run Flask on a different local port and update `VITE_API_URL` and `FRONTEND_ORIGIN` to match. Restart Vite after changing the API URL and restart Flask after changing CORS settings.

## Blank page or frontend cannot be reached

**Symptom:** browser cannot open the site, or the page is blank. **Check:** Vite terminal says it is ready; use the printed local URL (normally `http://localhost:5173`); inspect browser console for import/runtime errors and run `npm run build` from `frontend`. Verify the exact route exists in `frontend/src/router/index.js`; unknown paths use a 404 placeholder. Restart Vite after dependency or environment changes.

## API request fails, CORS, or login does not persist

**Symptom:** network error, CORS rejection, 401/403, or user is immediately signed out. **Check:** Flask is running at `http://localhost:5000`, `/api/v1/health` responds, frontend's `VITE_API_URL` points to `/api/v1`, and `FRONTEND_ORIGIN` exactly matches the browser origin including scheme and port. API requests use credentialed cookies and CSRF headers. Keep the default credentialed CORS and CSRF protections enabled; align the origins instead of disabling security checks. Restart Flask and Vite after configuration changes.

## SQLite says table/column is missing or schema is old

**Symptom:** SQL error mentions a missing table or column. **Check:** from `backend`, with the correct `.env` and database URL loaded, run `python -m flask --app run.py init-data`, then query `/api/v1/health` and inspect Flask output. `init-data` creates missing tables and applies the known additive compatibility updates; it does not erase records. Back up first. If a column is outside the implemented updates, it needs a reviewed schema change; this repository has no general migration tool. Never delete the database as the default fix.

## Redis is unavailable

**Symptom:** Flask logs Redis/cache connection warnings or job enqueue fails. **Check:** Redis service/container is running, port 6379 is reachable, and `REDIS_URL`, `CELERY_BROKER_URL`, and `CELERY_RESULT_BACKEND` are valid. The cache falls back to SQLite on Redis read failures, but queued exports and monthly reports require the broker and worker. Start the documented Redis service and confirm its logs; then retry the job. Do not change database contents to address a cache issue.

## Celery tasks are not running

**Symptom:** export/report stays requested/queued, or scheduled reminders do not occur. **Check:** Redis broker is reachable, worker terminal is running `celery -A run:celery worker --loglevel=INFO --pool=solo` from `backend`, and Beat is running separately with `celery -A run:celery beat --loglevel=INFO`. Verify the processes use the same `.env`, database URL, and broker. A worker is needed for queued work; Beat is additionally needed for schedules. Read task tracebacks in worker output. Restart worker after task changes and Beat after schedule/config changes.

## Admin login or seeded password does not work

**Symptom:** local Admin receives invalid credentials. **Check:** confirm the correct local DB is selected by `DATABASE_URL`; first Admin email/password were set in `backend/.env` before `init-data`; and account is active. `init-data` does not reset an existing Admin. If you can sign in, change credentials through **Settings → Admin sign-in details**, confirming the current password. If locked out, back up the database, set a private `ADMIN_EMAIL` and `ADMIN_PASSWORD` in `backend/.env`, then from `backend` run `python -m flask --app run.py reset-admin`. This updates the first Admin by ID; it rejects placeholder credentials and email conflicts. Keep `.env` private and do not put the password in a command argument. Do not edit password hashes manually. There is no email-based recovery flow.

## Booking fails because slot is full or window closed

**Symptom:** server rejects the Student booking. **Likely causes:** capacity is exhausted, exam is not in the booking lifecycle state, current time is outside `booking_start`/`booking_end`, slot is cancelled/completed, Student already has an active booking for the exam, or account is inactive. **Check:** Admin **Examinations** lifecycle and booking dates, **Slots** capacity/occupancy, and Student **My Bookings**. Choose another available slot or have Admin correct dates/capacity through the UI where permitted. Server capacity and duplicate checks remain authoritative.

## Examination is missing from the Student dashboard

**Symptom:** an Admin changed examination status but the Student does not see it. **Check:** `slot_creation` means the Examiner is preparing slots, so it appears as **Preparing** and cannot yet be booked. For booking, the status must be `booking_open`, the course must be active, and the current time must be inside `booking_start` and `booking_end`. A future booking window appears as **Opening soon**; an expired one is hidden. The dashboard refreshes every 30 seconds, on tab focus, or immediately with **Refresh**. The **Browse examinations** page lists only exams currently inside their booking window.

If the dashboard says booking is open but **Browse examinations** does not show it, refresh the page and confirm both booking dates are present, the start is not in the future, the end has not passed, and the course is active. Student browse/detail reads are served fresh from the database. **My Bookings** stays empty until the Student books a slot; use **Browse examinations** to find exams. If an exam opens but says no available slots, an Examiner must create future slots during its slot-creation window.

## Exam time shifts after editing

The admin edit form shows stored UTC timestamps in the browser's local time and converts the chosen time back to UTC when saving. API timestamps must include an explicit UTC timezone (`Z`); otherwise the browser may parse the UTC clock time as local and shift the exam time on a subsequent edit.

## Examination edit is rejected after changing status

The lifecycle status is changed using the row's status control. The edit form should submit only editable examination fields; sending the entire API response includes server-managed fields that the API rejects. Current lifecycle status is preserved when saving ordinary edits.

## Results are not visible

**Symptom:** Student **Results** does not show an evaluation. **Check:** Examiner submitted it (a saved draft is private), and Admin published it under **Evaluations**. Only published scores appear in Student results. Ask the assigned Examiner/Admin to complete their workflow; do not expose unpublished marks by direct API/database access.

## Cached information looks outdated

**Symptom:** list appears stale after a change. **Check:** refresh the screen; confirm the mutation succeeded and Flask logged no Redis failure. Relevant writes invalidate the cache, but cache settings/Redis outages may delay observations. Redis cache errors fall back to SQLite. Restart Redis only if its service is unhealthy; do not delete or edit SQLite rows. If you changed cache code/configuration, restart Flask and retest the mutation/read flow.

## Report email is not sent

**Symptom:** report history shows email not configured or delivery failure. **Check:** report artifact generation status first, then `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, credentials if required, and `REPORT_EMAIL`. For local use, email is optional: download the generated HTML report, and use in-app reminders. Never commit real SMTP credentials.

## Student CSV export fails

Open the Student **Dashboard**, select **Request export**, then use **Download CSV** when the job shows completed. The export is generated directly and does not need Redis or a Celery worker. If the job shows failed, check the Flask terminal for `Student export failed`; verify that `EXPORT_DIR` is writable and that the SQLite database can read the Student's bookings. Retry after fixing the reported server-side problem.

## Safe diagnostic commands

Run from `backend`:

```powershell
python -m flask --app run.py routes
Invoke-RestMethod http://localhost:5000/api/v1/health
python -m pytest -q
```

Run from `frontend`:

```powershell
npm run build
```

These commands do not delete or reset the database.
