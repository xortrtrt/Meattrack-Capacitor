# MEATTRACK Website

FastAPI prototype for Batangas Premium's MEATTRACK public website and role-based portals.

## Framework and Stack

- Backend framework: FastAPI
- Template engine: Jinja2 server-rendered HTML
- Frontend: HTML, CSS, and vanilla JavaScript
- Database: PostgreSQL
- Database driver: psycopg2
- Local app server: Uvicorn
- Local database runtime: Docker PostgreSQL container

This project is not using React, Vue, Angular, Laravel, Django, or Node.js for the main app.

## Run Locally

Start the PostgreSQL Docker container first. The current local database connection expects:

```text
postgresql://meattrack:meattrack@127.0.0.1:5433/meattrack
```

Create your local environment file:

```powershell
Copy-Item .env.example .env
```

Then edit `.env` and fill in local secrets such as `DATABASE_URL`, `POSTGRES_PASSWORD`, `SESSION_SECRET_KEY`, `OPENROUTER_API_KEY`, and demo account passwords. The real `.env` file is ignored by Git.

If the database is empty or needs a reset, run:

```powershell
.venv\Scripts\python.exe tools\seed_database.py
```

The seed script also imports every file from `app/static/img` into the PostgreSQL `media_assets` table. If you add or replace image files later without resetting the database, run:

```powershell
.venv\Scripts\python.exe tools\import_static_images.py
```

Then start FastAPI:

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

## Demo Logins

The login form has one email/password flow. Credentials are verified against the `accounts` table in PostgreSQL, and the account type determines which dashboard opens.

- Owner: `patric.mapa@gmail.com` / `demo123`
- Team Leader: `leader@batangaspremium.test` / `demo1234`
- Reseller: `reseller@lipafresh.test` / `demo1234`

Passwords are stored in `accounts.password_hash`. The seed script stores PBKDF2 password hashes, and old plain-text local demo passwords are upgraded to hashes after a successful login.

## Current Implementation

- Public landing page for Batangas Premium.
- Reseller Portal: dashboard, ordering, order history, sales reports, messages.
- Team Leader Portal: daily dashboard, walk-in sales, recipe-based production, alerts, reseller inquiry approval/rejection, reseller order handling, reports.
- Owner Portal: executive dashboard, product pricing, reports, forecasts, account management, audit logs.
- Portal pages use `app/templates/portals/base.html` plus one role template per portal: `reseller.html`, `team_leader.html`, and `owner.html`.
- CSS is split by surface: `public.css` for public pages, `login.css` for login, `portal_base.css` for shared portal layout, and `app/static/css/portals/` for role-specific portal overrides.
- `app/repositories.py` reads and writes PostgreSQL data for the current UI flows.
- Image assets are stored in PostgreSQL `media_assets` as binary data and served through `/media/{filename}`.
- PostgreSQL schema lives in `database/schema.sql` and is intentionally simplified to the portal workflows currently implemented.

## Chatbot Configuration

The Batangas Premium support chatbot uses the OpenRouter-compatible OpenAI client format when an API key is configured.

```powershell
$env:OPENROUTER_API_KEY="your_openrouter_key"
$env:OPENROUTER_MODEL="openai/gpt-4o-mini"
```

If `OPENROUTER_API_KEY` is not set, the app uses a local fallback that only answers from the approved Batangas Premium FAQ information.
