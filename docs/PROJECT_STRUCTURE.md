# Project Structure

This is a two-part application: Flask/SQLAlchemy in `backend` and Vue/Vite in `frontend`. The `sources/` folder is a synced reference area and must remain read-only.

```text
.
├── README.md
├── AGENTS.md
├── docs/
│   ├── INSTALLATION_AND_RUN_GUIDE.md
│   ├── MANUAL_UPDATE_GUIDE.md
│   ├── PROJECT_STRUCTURE.md
│   ├── PROJECT_STATUS.md
│   └── TROUBLESHOOTING.md
├── backend/
│   ├── .env.example
│   ├── requirements.txt
│   ├── run.py
│   ├── config.py
│   ├── app/
│   │   ├── __init__.py
│   │   ├── models.py
│   │   ├── extensions.py
│   │   ├── celery_app.py
│   │   ├── tasks.py
│   │   ├── api/
│   │   │   ├── routes.py
│   │   │   ├── admin_routes.py
│   │   │   ├── portal_routes.py
│   │   │   └── jobs_routes.py
│   │   └── services/
│   │       ├── auth.py
│   │       └── cache.py
│   ├── tests/
│   └── instance/              # generated DB, exports, reports (ignored by Git)
└── frontend/
    ├── package.json
    ├── package-lock.json
    ├── vite.config.js
    ├── index.html
    └── src/
        ├── main.js
        ├── App.vue
        ├── api.js
        ├── state.js
        ├── router/index.js
        ├── components/AppShell.vue
        ├── views/
        │   ├── Login.vue
        │   ├── Register.vue
        │   ├── Dashboard.vue
        │   ├── Profile.vue
        │   ├── AdminPage.vue
        │   ├── PortalPage.vue
        │   └── Placeholder.vue
        ├── style.css
        └── admin.css
```

## Backend files

- `backend/run.py` creates the Flask application and exposes the configured Celery app to the CLI.
- `backend/config.py` reads environment variables, sets the default SQLite URI, session/CORS settings, cache, Celery, email, and artifact paths.
- `backend/.env.example` lists safe local configuration keys. Copy it to the ignored `backend/.env`; replace placeholders privately.
- `backend/app/__init__.py` creates Flask, registers the API, sets up CSRF/CORS, and defines `init-db`, `seed-admin`, `init-data`, and the local `reset-admin` credential-recovery command. `upgrade_sqlite_schema()` performs known additive SQLite compatibility updates.
- `backend/app/models.py` defines users/profiles, courses, examinations, rubric criteria, slots, bookings, private student document submissions, evaluations, notifications, Gmail connection credentials, reports, export jobs, reminder logs, and audit logs.
- `backend/app/extensions.py` creates the shared SQLAlchemy and CSRF objects.
- `backend/app/api/routes.py` contains health, authentication, profiles, and dashboards.
- `backend/app/api/admin_routes.py` implements Admin catalogue, user, slot, booking, rubric, reports summary, settings, and audit operations.
- `backend/app/api/portal_routes.py` implements Examiner/Student portal actions, evaluation and result publication.
- `backend/app/api/google_mail_routes.py` provides Admin-only Gmail sender connection controls. Account authentication uses email and password routes.
- `backend/app/services/notifications.py` centralizes in-app and email notifications; student submission files are stored privately under the configured instance submission directory.
- `backend/app/api/jobs_routes.py` implements report and Student CSV export API operations.
- `backend/app/services/auth.py` contains Student registration and idempotent initial Admin creation.
- `backend/app/services/cache.py` manages Redis-backed JSON cache reads, namespaces, and invalidation. Cache read errors fall through to SQLite.
- `backend/app/celery_app.py` configures Redis broker/result backends and Beat schedules.
- `backend/app/tasks.py` implements reminders, monthly reports, Student CSV exports, retries, email delivery, and artifact cleanup.
- `backend/tests/` contains API, role portal, Admin, and milestone task tests. Tests use isolated in-memory SQLite; task tests use eager mode/fakes rather than proving live Redis delivery.
- Default runtime database/artifact location is under `backend/instance/`; runtime folders and database files are ignored by Git.

## Frontend files

- `frontend/package.json` defines `npm run dev`, `npm run build`, and `npm run preview`.
- `frontend/package-lock.json` pins npm dependency versions; use `npm ci` for repeatable setup.
- `frontend/vite.config.js` configures the Vue plugin for Vite.
- `frontend/src/main.js` starts Vue and loads Bootstrap/CSS.
- `frontend/src/router/index.js` defines login/register, dashboards, role-protected routes, and page sections.
- `frontend/src/api.js` sets the API base URL, sends session credentials and CSRF headers, and downloads files.
- `frontend/src/state.js` holds shared auth and notification state.
- `frontend/src/components/AppShell.vue` renders navigation, account identity, and notifications.
- `frontend/src/views/AdminPage.vue` renders Admin courses, exams, rubrics, users, slots, bookings, results, reports, settings, and audit screens.
- `frontend/src/views/PortalPage.vue` renders Examiner and Student tasks, profiles, bookings, evaluations, and scorecards.
- `frontend/src/components/ExaminerSlotsTable.vue` displays concise Examiner slot availability using 12-hour times. `frontend/src/components/WorkSubmissionModal.vue` handles private student document submission and download.
- `frontend/src/views/Login.vue` and `Register.vue` provide sign-in and Student self-registration.
- `frontend/src/views/Dashboard.vue` and `Profile.vue` render common dashboard and profile pages.
- `frontend/src/views/Placeholder.vue` is the not-found fallback; it is not a substitute for a missing workflow.
- `frontend/src/style.css` and `frontend/src/admin.css` define global and portal styles.
- `frontend/dist/` is generated by `npm run build`. It is not served by the backend in this repository.

## Data flow

The browser sends credentialed requests to `/api/v1` on Flask. The Flask API enforces session roles, ownership, CSRF for mutations, and validation. SQLAlchemy reads/writes SQLite. Selected reads may use Redis; Celery uses Redis for jobs. The worker writes private report/export artifacts under the configured artifact folders. Student result APIs only expose published evaluations.
