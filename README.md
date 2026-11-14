# Examination Management Portal (EMP)

The **Examination Management Portal (EMP)** is a web application developed to simplify the process of managing examinations in an academic institution.

It provides separate dashboards for Admins, Examiners, and Students. Admins can manage examinations, courses, exam slots, bookings, and results. Examiners can view their assigned slots and evaluate student submissions. Students can book available slots, submit their work, and check their results.

The application is built using **Flask, Vue 3, SQLite, Redis, and Celery**.

## Technologies Used

- **Backend:** Python and Flask
- **Frontend:** Vue 3
- **UI Styling:** Bootstrap
- **Database:** SQLite
- **Caching and Background Jobs:** Redis and Celery
- **Email:** SMTP / Gmail API
- **Version Control:** Git and GitHub

## Getting Started

### Requirements

Make sure the following are installed:

- Python 3.10 or above
- Node.js 18 or above
- npm
- Git
- Redis (required for background and scheduled tasks)

For Windows users, Docker Desktop can be used to run Redis.

### 1. Clone the repository

Open PowerShell and run:

```powershell
git clone https://github.com/24f2002384/Examination-Management-System.git
cd Examination-Management-System
code .
```

### 2. Set up the backend

Open a terminal in VS Code:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Open `backend/.env` and configure the required values. In particular, set a private `SECRET_KEY` and your own Admin email and password before initializing the database.

Keep your actual `.env` file private. It should not be uploaded to GitHub.

### 3. Start Redis

If you are using Docker Desktop, run:

```powershell
docker run --name emp-redis --rm -p 6379:6379 redis:7-alpine
```

Keep this terminal running.

### 4. Initialize the database and start Flask

From the `backend` directory:

```powershell
python -m flask --app run.py init-data
python -m flask --app run.py run --debug
```

The Flask API will be available at:

`http://localhost:5000`

### 5. Start the frontend

Open another VS Code terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open the application in your browser:

`http://localhost:5173`

The API health check is available at:

`http://localhost:5000/api/v1/health`

### 6. Start background services

Redis and Celery are used for scheduled tasks such as reminders, monthly reports, and cleanup.

Open another terminal in the `backend` directory, activate the backend virtual environment if necessary, and run:

```powershell
celery -A run:celery worker --loglevel=INFO --pool=solo
```

Open one more backend terminal and run:

```powershell
celery -A run:celery beat --loglevel=INFO
```

The Student CSV export can be used without Celery. Background and scheduled features require the relevant services to be running.

## Main Features

### Admin

- Manage courses and examinations.
- Create and manage Examiner accounts.
- Activate or manage Student accounts.
- Configure examination rubrics and slots.
- View and manage student bookings.
- Review submitted evaluations and publish results.
- Access reports, audit history, and dashboard information.
- Manage profile and sign-in credentials.

### Examiner

- View assigned examination slots.
- Check booking and slot details.
- Download student work submissions.
- Evaluate submissions using the configured rubric.

### Student

- Register and sign in using email and password.
- View available examinations and slots.
- Book or cancel examination slots.
- Upload work documents for booked examinations.
- View examination history and published results.
- Export examination history as a CSV file.
- Receive in-app notifications.

## Additional Information

- Students can upload PDF, DOC, DOCX, and TXT files up to 10 MB.
- Uploaded submissions are stored locally under `backend/instance/submissions`.
- Demo course and examination data can be added using:

```powershell
python -m flask --app run.py seed-demo
```

This command adds sample catalogue data without creating or changing user accounts.

- The database is initialized using the `init-data` command. It creates the required tables and ensures that the configured Admin account exists.
- Back up the SQLite database before making schema changes. Do not delete the database to resolve errors.

## Gmail Email Configuration

The application supports SMTP by default and also includes an optional Gmail API integration for sending emails.

Gmail API authentication is used for the application's email delivery service. Students, Examiners, and Admins continue to sign in using their email addresses and passwords.

For the complete Gmail API setup, OAuth configuration, and testing instructions, refer to:

[Google Gmail OAuth Guide](docs/GOOGLE_GMAIL_OAUTH_GUIDE.md
