# MEATTRACK Website

FastAPI prototype for Batangas Premium's MEATTRACK public website and role-based portals.

## Framework and Stack

- Backend framework: FastAPI
- Template engine: Jinja2 server-rendered HTML
- Frontend: HTML, CSS, and vanilla JavaScript
- Database: Supabase PostgreSQL in production; local PostgreSQL for development
- Database driver: psycopg2
- Local app server: Uvicorn
- Local database runtime: Docker PostgreSQL container
- Mobile runtime: Capacitor 8 (Android native project)

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

The seed script creates application records only. Images remain available from
`app/static/img` locally and are uploaded separately to Supabase Storage for
production.

Then start FastAPI:

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

The deployment health check is available at `GET /health` and verifies both the
FastAPI service and its database connection.

## Supabase Database

MEATTRACK keeps all database operations on the FastAPI server. The mobile app
does not contain the Supabase database password or connect directly to the
database.

1. Create a Supabase project and copy its direct or Session pooler connection
   string from **Connect**. Session mode on port 5432 is usually the simplest
   option for an IPv4-hosted FastAPI service.
2. URL-encode special characters in the database password.
3. Import the existing schema and data into a new/disposable project:

```powershell
$env:SUPABASE_DB_URL="postgresql://postgres.PROJECT_REF:URL_ENCODED_PASSWORD@REGION.pooler.supabase.com:5432/postgres"
.venv\Scripts\python.exe tools\migrate_to_supabase.py --reset
```

4. Set the hosted backend's `DATABASE_URL` to that Session pooler URL and append
   `?sslmode=require`. Set a strong `SESSION_SECRET_KEY` there as well.

The importer replaces only MEATTRACK tables, verifies key row counts, and then
enables RLS without public policies. That prevents Supabase anon/authenticated
API keys from bypassing FastAPI's authentication and business rules. See
`database/README.md` for details.

## Supabase Storage Images

Production product and branding images are served from a public Supabase Storage
bucket rather than PostgreSQL. Run the idempotent uploader locally with a secret
key from **Project Settings -> API Keys**:

```powershell
$env:SUPABASE_URL="https://PROJECT_REF.supabase.co"
$env:SUPABASE_SECRET_KEY="YOUR_LOCAL_SECRET_KEY"
.venv\Scripts\python.exe tools\migrate_images_to_storage.py
```

The tool creates or updates the public `meattrack-assets` bucket, restricts it to
JPEG/PNG files up to 5 MB, uploads `app/static/img` under `images/`, and verifies
every uploaded file by size and SHA-256. It is safe to run again. Never place
`SUPABASE_SECRET_KEY` in Render, GitHub, Capacitor, templates, or browser code.

Copy the printed public folder URL into `MEDIA_BASE_URL` on Render. When that
variable is empty, the app falls back to `/static/img` for local development.
The old `media_assets` table and `tools/import_static_images.py` are retained for
one rollback release but are no longer used by page requests or database seeding.

## Capacitor Mobile App

Because the current UI is rendered by FastAPI/Jinja, the native shell loads the
deployed FastAPI site over HTTPS. Deploy the backend first (the included
`Dockerfile` is ready for a container host), and confirm that
`https://YOUR_DOMAIN/health` returns `{"status":"ok","database":"connected"}`.

Install and generate the Android project:

```powershell
npm.cmd install
npm.cmd exec cap add android
```

Select the deployed backend and sync it into Android:

```powershell
$env:MOBILE_APP_URL="https://YOUR_DOMAIN"
npm.cmd run mobile:sync
npm.cmd run mobile:android
```

Capacitor 8 requires Android Studio 2025.2.1 or newer and an Android SDK. Android
Studio supplies the matching JDK. Every time the mobile URL or Capacitor
configuration changes, run `npm.cmd run mobile:sync` again. The app refuses
plain HTTP by default.

For same-Wi-Fi development only, start Uvicorn on all interfaces and explicitly
allow a LAN URL:

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
$env:MOBILE_APP_URL="http://YOUR_COMPUTER_IPV4:8000"
$env:CAPACITOR_ALLOW_CLEARTEXT="true"
npm.cmd run mobile:sync
```

If the local computer cannot run Android Studio, use the repository's
**Build Android APK** GitHub Actions workflow. Choose **Run workflow**, enter the
deployed FastAPI HTTPS URL, and download the `meattrack-android-debug` artifact
after the job succeeds. The runner validates `/health`, installs Android SDK 36,
syncs Capacitor, and builds the APK entirely in the cloud.

The native iOS project is also included. For a free cloud build, use the
**Build iOS Simulator App** GitHub Actions workflow, enter the same deployed
FastAPI HTTPS URL, and download the `meattrack-ios-simulator` artifact. It uses
macOS 26 and Xcode 26 to build an unsigned iOS Simulator `.app` bundle.

The simulator artifact cannot be installed on a physical iPhone. A signed IPA,
TestFlight build, or App Store release requires Apple signing credentials and an
Apple Developer Program membership. Without signing, iPhone users can still open
the deployed site in Safari and choose **Share -> Add to Home Screen**.

## Deploy FastAPI on Render

The included `render.yaml` defines a free Docker web service in Singapore. In
Render, create a new Blueprint from this GitHub repository and supply these
secret values when prompted:

- `DATABASE_URL`: the Supabase Session pooler URL on port 5432;
- `MEDIA_BASE_URL`: the public Supabase Storage `meattrack-assets/images` URL;
- `BREVO_API_KEY`: required on Render Free for login OTP and account emails;
- `BREVO_FROM_EMAIL`: a verified Brevo sender email, for example your Gmail address;
- `BREVO_FROM_NAME`: optional display name, defaults to `Batangas Premium`;
- `RESEND_API_KEY`: optional alternate HTTPS email provider;
- `RESEND_FROM_EMAIL`: required only if using Resend instead of Brevo;
- `OPENROUTER_API_KEY`: optional; leave empty to use the local chatbot fallback.

Render Free blocks outbound SMTP ports, so Gmail SMTP is only a local fallback.
Production email delivery uses Brevo first when `BREVO_API_KEY` is set, then
Resend if configured, then SMTP only for local or paid environments.

Render generates `SESSION_SECRET_KEY` automatically. Once `/health` reports a
connected database, use the service's `https://...onrender.com` URL as the
`mobile_app_url` input to the Android build workflow.

The service intentionally remains on Render's free instance type. It can spin
down after inactivity, so the first request may still be slow; warm portal
requests use section-specific database reads.

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
- `app/repositories.py` reads and writes PostgreSQL data for the current UI flows; portal pages load only the datasets needed by their selected section.
- Production images use the Supabase Storage public CDN. `/media/{filename}` remains as a compatibility redirect and performs no database query.
- Rubik and Lucide are pinned, licensed, and served locally instead of loading from Google Fonts or Unpkg.
- PostgreSQL schema lives in `database/schema.sql` and is intentionally simplified to the portal workflows currently implemented.

## Tests

Install the development dependencies and run the regression suite:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
```

The suite covers every portal role/section, exact data-loader selection,
parameterized filters, the single-query dashboard metrics implementation,
Storage upload verification, compatibility redirects, and local asset caching.

## Chatbot Configuration

The Batangas Premium support chatbot uses the OpenRouter-compatible OpenAI client format when an API key is configured.

```powershell
$env:OPENROUTER_API_KEY="your_openrouter_key"
$env:OPENROUTER_MODEL="openai/gpt-4o-mini"
```

If `OPENROUTER_API_KEY` is not set, the app uses a local fallback that only answers from the approved Batangas Premium FAQ information.
