# Installation and Run Guide (Windows 10/11)

These steps match the current repository. Run each command from the folder stated above its code block. Keep secrets private; `backend/.env` is ignored by Git.

## 1. Prerequisites

Install:

- Python 3.10 or newer. During installation select **Add Python to PATH** if offered.
- Node.js 18 or newer (includes npm).
- VS Code or another editor (optional).
- Redis 6.2 or newer for Redis caching and queued background jobs. This project does not include a Windows Redis installer. Docker Desktop with Linux containers is a supported local option. Alternatively, install and run Redis inside WSL2 and make sure port 6379 is reachable from Windows. Redis is not required just to run the main SQLite-backed pages; Celery jobs require the configured broker and a running worker.
- Git is optional; it is not needed to run a downloaded project folder.

Check Python and Node from PowerShell:

```powershell
python --version
node --version
npm --version
```

If `python` is unavailable but the Windows Python launcher exists, use `py -3 --version` and replace `python` with `py -3` in later commands.

## 2. Open the project

Open PowerShell, move into the project root (the folder containing `backend` and `frontend`), then start VS Code:

```powershell
Set-Location 'C:\path\to\Examination-Management'
code .
```

Replace the sample folder path with the actual location. You can use another editor if you prefer.

## 3. Set up the backend and local environment

In Terminal 2, from the project root:

```powershell
Set-Location .\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

If PowerShell blocks virtual-environment activation, use this only for the current terminal and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Open `backend/.env` in the editor. Replace `SECRET_KEY` with a long random local secret and set unique `ADMIN_EMAIL` and `ADMIN_PASSWORD` values. Keep the `.env` file private and do not paste its values into screenshots or reports. The example file has placeholders, not usable shared credentials. Other settings and defaults are described in the Environment section below.

The Flask CLI loads `.env` from the current working directory when `python-dotenv` is installed (it is included in `requirements.txt`), so start Flask commands from `backend`.

## 4. Set up the frontend

In Terminal 3, from the project root:

```powershell
Set-Location .\frontend
npm ci
```

`npm ci` uses the committed `frontend/package-lock.json` and reproduces the locked dependency versions. Use `npm install` only when intentionally changing dependencies and updating the lockfile.

## 5. Start Redis

For Docker Desktop, open Terminal 1 and run:

```powershell
docker run --name emp-redis --rm -p 6379:6379 redis:7-alpine
```

Leave it open. If port 6379 is already used by a Redis service, do not start a second container; use the existing service if its URL matches `REDIS_URL`. For WSL2, start Redis from the WSL distribution and verify Windows can reach the configured host and port. Redis is not part of the Python or npm installation.

## 6. Initialize SQLite and the Admin account

In Terminal 2, still in `backend` with `.venv` active and `.env` configured:

```powershell
python -m flask --app run.py init-data
```

This creates missing tables, runs the app's additive SQLite column/index compatibility procedure, and creates the first Admin using `ADMIN_EMAIL` and `ADMIN_PASSWORD` if there is no Admin yet. It does not reset existing data or change an existing Admin's password. While signed in, change credentials through Admin **Settings → Admin sign-in details**. If the Admin cannot sign in, set the desired credentials in `.env`, back up the database, and run the recovery command from `backend`:

```powershell
python -m flask --app run.py reset-admin
```

The command resets the first Admin account by ID (or seeds one if there is none); it refuses built-in placeholder credentials and email conflicts. Keep `.env` private. There is no email-based reset/verification flow. The project also has no general migration framework; see [Manual Update Guide](MANUAL_UPDATE_GUIDE.md) for safe schema/database procedures.

## 7. Start Flask

In Terminal 2, from `backend`:

```powershell
python -m flask --app run.py run --debug
```

Flask listens at `http://localhost:5000`. The API base is `http://localhost:5000/api/v1`; its health check is `http://localhost:5000/api/v1/health` and reports both application and database status. The Flask development server is for local development, not a public production deployment.

## 8. Start Vue

In Terminal 3, from `frontend`:

```powershell
npm run dev
```

Open `http://localhost:5173` (Vite prints its actual local URL). The default frontend API address is `http://localhost:5000/api/v1`. To change it for local development, create `frontend/.env.local` with `VITE_API_URL=http://localhost:5000/api/v1`, restart Vite, and make sure `FRONTEND_ORIGIN` in `backend/.env` exactly matches the page origin. Vite environment values are embedded into browser code; never put secrets there.

## 9. Start background work

Redis, Flask, and the database must be available first. In Terminal 4, from `backend` with the virtual environment active:

```powershell
celery -A run:celery worker --loglevel=INFO --pool=solo
```

In Terminal 5, also from `backend`:

```powershell
celery -A run:celery beat --loglevel=INFO
```

The `solo` pool is used for local Windows development. The worker runs monthly reports, reminders, and cleanup tasks; Student history CSV export is queued through Celery. Beat schedules reminders at `REMINDER_HOUR` (08:00 by default), monthly reports at `MONTHLY_REPORT_HOUR` (01:00 by default) on the first day of each month, and artifact cleanup at 02:00, using `APP_TIMEZONE`. Keep both terminals running for scheduled jobs. Email is optional; without provider configuration, in-app notifications remain available and monthly reports are stored for download.

## Recommended terminal layout

1. Terminal 1 — Redis, only if not already running as a service.
2. Terminal 2 — backend virtual environment, database initialization, then Flask.
3. Terminal 3 — frontend dependencies, then Vite.
4. Terminal 4 — Celery worker when asynchronous jobs are needed.
5. Terminal 5 — Celery Beat when scheduled jobs are needed.

## 10. Sign in and check the workflows

Visit `http://localhost:5173`.

- **Admin:** sign in with the `ADMIN_EMAIL` and `ADMIN_PASSWORD` set before the first initialization. Change credentials later through **Settings → Admin sign-in details**, or use the local recovery command if locked out. Create an active course, an examination, assign an active Examiner, set schedule windows, add rubric criteria, and move the exam lifecycle forward. Create an Examiner from **Examiners**; the UI displays a generated initial password once. Configure it securely and deliver it to the intended person yourself.
- **Examiner:** sign out and sign in using the account/password created by Admin. Under **My Examinations**, inspect assigned exams. Under **My Slots**, create slots while the slot-creation window is open. Review students and enter criterion marks/remarks, save a draft, then submit.
- **Student:** use **Create your account** on the login page (registration creates Student accounts only). Browse examinations while booking is open, inspect details, book an available slot, then view booking/history. Student scorecards appear only after the Examiner submits and the Admin publishes the result.

For field-by-field workflows, use [Manual Update Guide](MANUAL_UPDATE_GUIDE.md). For test credentials, no shared defaults are provided; use the local Admin you configured and the Examiner account you created.

## Development server and production build

Vite's `npm run dev` serves source files with hot reload at `http://localhost:5173`. It is intended for local development and makes API calls to Flask. To create an optimized static build, from `frontend` run:

```powershell
npm run build
```

The output is `frontend/dist`. To preview that build locally, run:

```powershell
npm run preview
```

Vite prints a local preview URL. This repository does not configure production hosting, reverse proxy, HTTPS, or deployment. A successful build alone does not make the portal publicly reachable. A deployment must separately serve `dist`, provide the API, set the correct `VITE_API_URL` at build time and configure trusted HTTPS/CORS/session settings.

## Environment variables

The source example is `backend/.env.example`; the local file is `backend/.env`.

| Variable | Current purpose/default |
|---|---|
| `SECRET_KEY` | Flask session/CSRF signing; example is a placeholder. Set a private random value. |
| `DATABASE_URL` | Defaults to `sqlite:///emp.db`, stored under Flask's `backend/instance/emp.db`. |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD` | Used only to create an Admin when none exists. |
| `FRONTEND_ORIGIN` | Credentialed CORS origin; default `http://localhost:5173`. Must match browser origin. |
| `REDIS_URL` | Redis cache default `redis://localhost:6379/0`. |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Celery broker and result store; defaults in `.env.example` use Redis databases 0 and 1. |
| `CACHE_ENABLED`, `CACHE_DEFAULT_TIMEOUT` | Redis cache toggle and default TTL (true, 300 seconds). |
| `APP_TIMEZONE`, `REMINDER_HOUR`, `MONTHLY_REPORT_HOUR` | Celery Beat timezone and schedule controls. Daily reminders default to 08:00 and monthly reports default to 01:00 on the first day of the month. |
| `REMINDER_DELIVERY` | `email` (default). Reminder emails also create in-app notifications. |
| `CELERY_TASK_DEFAULT_RETRY_DELAY`, `CELERY_TASK_MAX_RETRIES`, `CELERY_TASK_SOFT_TIME_LIMIT`, `CELERY_TASK_TIME_LIMIT` | Background retry and time-limit settings. |
| `CELERY_TASK_ALWAYS_EAGER` | False by default: queue work for the worker. True executes work inline; useful in tests. |
| `EXPORT_DIR`, `REPORT_DIR` | Private generated artifact folders; defaults under `backend/instance`. |
| `EXPORT_RETENTION_DAYS`, `REPORT_RETENTION_DAYS` | Artifact cleanup age (7 and 365 days). |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, `REPORT_EMAIL` | Optional email settings. No real credentials belong in tracked files. |
| `COOKIE_SECURE` | Set true only when serving over HTTPS; false for local HTTP. |

`frontend/.env.local` may set `VITE_API_URL`; the frontend default is `http://localhost:5000/api/v1`.

## Check the API and registered routes

From `backend`:

```powershell
Invoke-RestMethod http://localhost:5000/api/v1/health
python -m flask --app run.py routes
```

The health check is read-only. Route listing confirms Flask registration but does not test every protected workflow.
