# MEATTRACK Handoff

## Project Snapshot

Repository: https://github.com/xortrtrt/Capstone.git

Local project path used during development:

```text
C:\Users\patri\OneDrive\Desktop\capstone
```

Current app status:

- FastAPI prototype is running locally with HTML, CSS, and JavaScript.
- Public landing page is focused on Batangas Premium as a business website.
- Login is a single process that redirects users based on role.
- Reseller, Team Leader, and Owner portal pages are scaffolded with demo flows.
- Data is currently demo/in-memory data, not PostgreSQL-backed yet.
- PostgreSQL schema and Mermaid ERD are already created in `database/`.

## Important Product Decisions

- The landing page should only describe the Batangas Premium business, products, reseller partnership, contact/inquiry details, and customer support.
- The landing page should not mention inventory dashboards or internal system features.
- There should not be separate login choices for Owner, Team Leader, and Reseller.
- Login should use one email/password form and redirect based on account role.
- Reseller inquiry approval is handled by the assigned Team Leader, not the Owner.
- Regular employees do not have login accounts.
- Team leaders are employees with accounts.
- Resellers are external accounts.
- Supplier ordering, online payments, tax computation, logistics, mobile apps, and government integrations are out of scope.

## Demo Accounts

All demo accounts use:

```text
Password: demo1234
```

Accounts:

```text
Owner:       owner@batangaspremium.test
Team Leader: leader@batangaspremium.test
Reseller:   reseller@lipafresh.test
```

## Setup for New Developers

Install these first:

- Git
- Python from https://www.python.org/downloads/

During Python installation on Windows, check:

```text
Add python.exe to PATH
```

Then run:

```powershell
git clone https://github.com/xortrtrt/Capstone.git
cd Capstone

python -m venv .venv
.\.venv\Scripts\activate

pip install -r requirements.txt
uvicorn app.main:app --reload
```

If `python` does not work, use:

```powershell
py -m venv .venv
.\.venv\Scripts\activate

pip install -r requirements.txt
py -m uvicorn app.main:app --reload
```

Open:

```text
http://127.0.0.1:8000
```

Frontend-only developers still need Python and the FastAPI dependencies because the pages are served through FastAPI templates.

PostgreSQL is not required yet for frontend-only work because the current app uses demo data.

## Daily Git Workflow

Get latest changes:

```powershell
git pull
```

Run the app:

```powershell
.\.venv\Scripts\activate
uvicorn app.main:app --reload
```

Save and upload changes:

```powershell
git add .
git commit -m "Describe the change"
git push
```

If dependencies are changed in `requirements.txt`, everyone should run:

```powershell
pip install -r requirements.txt
```

## Files and Responsibilities

Main FastAPI app:

```text
app/main.py
```

Demo data:

```text
app/demo_data.py
```

Chatbot logic:

```text
app/chatbot.py
```

Templates:

```text
app/templates/landing.html
app/templates/login.html
app/templates/portal.html
```

Styles:

```text
app/static/css/styles.css
```

Frontend JavaScript:

```text
app/static/js/app.js
```

Generated image assets:

```text
app/static/img/
```

Database schema and ERD:

```text
database/schema.sql
database/meattrack_erd.mmd
database/README.md
```

## Chatbot Requirements

The chatbot should follow this behavior:

- It is the official Batangas Premium customer support assistant.
- It answers only about products, prices, ordering, delivery, and reseller inquiries.
- It answers in 1 to 3 short sentences.
- It should not invent products, prices, requirements, schedules, or policies.

Approved products and prices:

```text
Pork Tocino: PHP 180 per pack
Pork Longganisa: PHP 160 per pack
Beef Tapa: PHP 220 per pack
Skinless Sausage: PHP 150 per pack
Bacon: PHP 250 per pack
Hungarian Sausage: PHP 210 per pack
```

Required unrelated reply:

```text
Sorry, I can only assist with Batangas Premium-related concerns.
```

Required fallback for unavailable details:

```text
Please contact Batangas Premium directly for complete details.
```

OpenRouter format requested by the user:

```python
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key="MY API KEY",
)
```

The app should read the real API key from environment variables, not from committed source code.

## Database Plan Summary

The database plan replaced `USERS` with `ACCOUNTS` and supports:

- Owner, Team Leader, and Reseller accounts.
- Employees without login accounts.
- Departments and active department leaders.
- Employee attendance, task completion, merit evaluations, and activity logs.
- Reseller inquiries assigned to Team Leaders.
- Inquiry approval/rejection by assigned Team Leader.
- Resellers linked optionally to inquiries.
- Raw material batches.
- Product recipes.
- Production consuming raw materials.
- Product batches as sellable inventory.
- Order batch allocations.
- Sales reports from Team Leaders and Resellers.
- Forecast runs and forecast results.
- Alerts for low stock and near expiry.
- Audit logs.
- Product price history and near-expiry batch discounts.

PostgreSQL rules:

- Use `numeric` for money and weight.
- Use `date` for production, expiry, and report periods.
- Use `timestamptz` for created, submitted, login, and audit timestamps.
- Add checks for positive quantities, nonnegative prices, valid scores, valid statuses, and valid account ownership.
- Keep dashboards and downloadable reports query-based unless exported files must be stored.

## Current Deployment Notes

For a VPS such as Hostinger:

- Do not upload `.venv/`, logs, caches, or `.env`.
- Preserve the project folder structure.
- Uploading files is not enough; the VPS must install dependencies and run the FastAPI app.
- Use a Linux virtual environment on the server.
- Use Uvicorn or Gunicorn/Uvicorn workers.
- Use Nginx as a reverse proxy for a domain.
- Use `systemd` so the app restarts automatically.
- PostgreSQL deployment will be needed later when real persistence is implemented.

Do not commit:

```text
.venv/
__pycache__/
*.log
.env
```

## Next Recommended Work

1. Continue improving frontend polish and responsive behavior.
2. Add remaining portal page details for Reseller, Team Leader, and Owner.
3. Replace demo/in-memory data with PostgreSQL database integration.
4. Add real authentication, password hashing, sessions, and email 2FA.
5. Wire forms to database-backed create/update flows.
6. Add backend validation matching `database/schema.sql`.
7. Add tests for login routing, inquiry approval, order lifecycle, inventory deduction, and invalid data rejection.
8. Prepare production deployment files such as `.env.example`, systemd service example, and Nginx config example.

