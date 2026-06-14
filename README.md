# MEATTRACK Website

FastAPI prototype for Batangas Premium's MEATTRACK public website and role-based portals.

## Run Locally

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Then open:

```text
http://127.0.0.1:8000
```

## Demo Logins

The login form has one email/password flow. The submitted account email determines which dashboard opens.

- Owner: `owner@batangaspremium.test`
- Team Leader: `leader@batangaspremium.test`
- Reseller: `reseller@lipafresh.test`
- Demo password: `demo1234`

## Current Implementation

- Public landing page for Batangas Premium.
- Reseller Portal: dashboard, ordering, order history, sales reports, messages.
- Team Leader Portal: daily dashboard, walk-in sales, inventory receiving, alerts, reseller inquiry approval/rejection, reseller order handling, employee attendance/tasks/merit forms, reports.
- Owner Portal: executive dashboard, product pricing, batch price adjustments, reports, forecasts, account management, audit logs.
- Seed/demo data is held in memory for immediate UI use.
- PostgreSQL schema remains in `database/schema.sql`; persistence wiring is the next implementation step.

## Chatbot Configuration

The Batangas Premium support chatbot uses the OpenRouter-compatible OpenAI client format when an API key is configured.

```powershell
$env:OPENROUTER_API_KEY="your_openrouter_key"
$env:OPENROUTER_MODEL="openai/gpt-4o-mini"
```

If `OPENROUTER_API_KEY` is not set, the app uses a local fallback that only answers from the approved Batangas Premium FAQ information.
