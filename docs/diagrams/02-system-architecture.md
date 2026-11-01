# MEATTRACK system architecture at different levels

Source review: **2026-10-31**. Levels 1-3 follow the context, container and component ideas of C4, expressed using ordinary Mermaid flowcharts. The logical layers and deployment views complement those levels. A module box is a responsibility inside the application, not a separately deployed service.

## Level 1: system context

```mermaid
flowchart TB
    V[Public visitor or prospective reseller]
    R[Reseller]
    S[Sales Team Leader]
    SS[Sales Supervisor]
    I[Inventory Team Leader]
    O[Owner]
    MT[MEATTRACK web-based business management system]
    V <-->|Catalog, inquiry, chatbot and live support| MT
    R <-->|Own orders, payments, returns and messages| MT
    S <-->|Assigned order review, counter sales and return tracking| MT
    SS <-->|Onboarding, assigned processing, refunds and delivery| MT
    I <-->|Materials, production, stock, custody and release| MT
    O <-->|Oversight, administration, reports and forecasting| MT
    MT -->|Transactional email| MAIL[Configured email delivery: Brevo in production]
    MT <-->|Public live-chat event delivery| ABLY[Ably]
    MT -.->|Optional hosted chatbot requests| AI[OpenRouter]
    PAY[External bank or payment provider]
    R -.->|Pays outside MEATTRACK| PAY
    SS -.->|Checks funds and manually transfers refunds| PAY
    O -.->|Checks funds and manually transfers refunds| PAY
```

Payment-provider activity is a human operational dependency. The inspected code records proof and confirmations; it does not integrate an automatic payment gateway or execute bank transfers.

## Level 2: runtime containers

```mermaid
flowchart TB
    B[Browser: HTML, CSS and vanilla JavaScript]
    N[Nginx: HTTPS reverse proxy]
    subgraph MT[MEATTRACK runtime]
        APP[FastAPI web process: routes, portals and repositories]
        WORKER[Python background worker: durable jobs and maintenance]
        DB[(PostgreSQL 16: business data, sessions, evidence and job state)]
        FILES[(Profile image upload volume)]
    end
    B <-->|HTTPS page requests and JSON polling| N
    N <-->|Loopback HTTP| APP
    APP <-->|Pooled SQL reads and transactions| DB
    WORKER <-->|Claim, update and complete persisted work| DB
    APP <-->|Profile image files| FILES
    APP -->|Login and password-change OTP email| MAIL[Configured email provider]
    WORKER -->|Queued activation, reset and inquiry emails| MAIL
    APP <-->|Publish events and issue scoped access| ABLY[Ably]
    WORKER -->|Retry failed public-chat publication| ABLY
    B <-->|Public live-chat events| ABLY
    APP -.->|Optional chatbot completion| AI[OpenRouter]
    WORKER --> MODEL[Local forecasting libraries: Prophet, pandas and baselines]
    APP --> OCR[Local receipt OCR: Tesseract and Pillow]
```

The application and worker use the same repository code and database. PostgreSQL provides the durable queues; no Redis, Celery broker, or independently deployed forecasting API is represented by the current implementation. Payment proof, return evidence, refund proof, report attachments and internal conversation media are database-backed; profile pictures use the upload volume.

## Level 3: web and worker components

```mermaid
flowchart TB
    UI[Jinja2 templates and static JavaScript]
    GUARD[Session middleware, CSRF, capabilities and ownership scope]
    subgraph WEB[FastAPI web application]
        PUB[Public, auth, chatbot and live-chat routers]
        PORTAL[Owner, team-leader, reseller, return, refund and report handlers]
        AUTH[Account, consent and session operations]
        ORDER[Cart, order and reservation repository]
        STOCK[Inventory products, materials, FEFO and movement repositories]
        RETURN[Returns, replacement workflow and custody repositories]
        MONEY[Refund repository and integrity checks]
        REPORT[Reporting, saved documents and Owner insights]
        CHAT[Public chat, reseller messaging and case conversations]
        FC[Forecast queue and run repository]
        DATA[Database connection pool and transaction helpers]
    end
    UI <-->|Forms and fetch requests| GUARD
    GUARD --> PUB
    GUARD --> PORTAL
    PUB --> AUTH
    PUB --> CHAT
    PUB --> ORDER
    PORTAL --> AUTH
    PORTAL --> ORDER
    PORTAL --> STOCK
    PORTAL --> RETURN
    PORTAL --> MONEY
    PORTAL --> REPORT
    PORTAL --> CHAT
    PORTAL --> FC
    ORDER --> STOCK
    RETURN --> STOCK
    RETURN --> MONEY
    REPORT --> MONEY
    AUTH --> DATA
    ORDER --> DATA
    STOCK --> DATA
    RETURN --> DATA
    MONEY --> DATA
    REPORT --> DATA
    CHAT --> DATA
    FC --> DATA
    DATA <--> DB[(PostgreSQL)]
    WORKER[Worker orchestration] --> FC
    WORKER --> ORDER
    WORKER --> CHAT
    WORKER --> MAINT[Outbox, expiry, cleanup and system-status operations]
    MAINT --> DATA
```

Arrows summarize calls and data access; they are not an exhaustive Python import graph. `app/repositories/__init__.py` re-exports domain functions as a compatibility facade.

## Level 4: representative transaction detail

This view expands one important operation rather than presenting every Python function as an architecture box. Related rejection and refund branches appear in the module flowcharts.

```mermaid
sequenceDiagram
    actor R as Reseller
    participant UI as Browser
    participant HTTP as Reseller payment handler
    participant OCR as Receipt validation helpers
    participant ORD as Orders repository
    participant FEFO as Allocation planner
    participant DB as PostgreSQL
    R->>UI: Upload receipt and confirm extracted details
    UI->>HTTP: Submit proof, signed scan token and payment details
    HTTP->>HTTP: Check session, CSRF and reseller scope
    HTTP->>OCR: Validate proof and confirmed payment
    OCR-->>HTTP: Validated metadata and attachment
    HTTP->>ORD: submit_order_payment
    ORD->>DB: Begin transaction and lock owning order
    ORD->>ORD: Check deadline, amount and duplicate reference
    ORD->>FEFO: Check eligible stock under product and batch locks
    FEFO->>DB: Read unexpired approved stock and competing reservations
    FEFO-->>ORD: Availability or shortage
    alt Sufficient stock
        ORD->>DB: Store proof, pending status and product reservations
        ORD->>DB: Record logs and notifications
    else Stock shortage after payment evidence
        ORD->>DB: Reject order and create payment-review refund case
        ORD->>DB: Notify reseller and assigned processor
    end
    ORD->>DB: Commit
    ORD-->>HTTP: Order result
    HTTP-->>UI: Redirect or response with current order state
```

## Logical layers and request path

```mermaid
flowchart TB
    L1[Presentation: Jinja2, HTML, CSS, browser JavaScript]
    L2[HTTP boundary: routers, portal actions and shared dependencies]
    L3[Business orchestration: scoped workflow and validation functions]
    L4[Persistence: domain repositories, SQL queries and transaction cursors]
    L5[(Storage: PostgreSQL and profile image files)]
    L1 <--> L2 <--> L3 <--> L4 <--> L5
    C[Cross-cutting controls: sessions, CSRF, capabilities, audit context and input validation]
    C -.-> L2
    C -.-> L3
    C -.-> L4
```

This is a logical separation. Domain rules and persistence are often implemented together in repository functions rather than in a distinct service class layer.

## Deployment view from checked-in production configuration

```mermaid
flowchart TB
    USER[User browser] -->|HTTPS| NGINX
    subgraph VPS[Contabo VPS: provider confirmed in project knowledge]
        NGINX[Host Nginx and TLS configuration]
        subgraph COMPOSE[Docker Compose services]
            APP[app: FastAPI / Uvicorn]
            WORKER[worker: python -m app.worker]
            MIGRATE[migrate: one-shot schema upgrade]
            PG[(db: PostgreSQL 16)]
        end
        DATA[(postgres_data persistent volume)]
        PROFILE[(profile_uploads persistent volume)]
        NGINX -->|127.0.0.1:8000| APP
        APP -->|Private backend network| PG
        WORKER -->|Private backend network| PG
        MIGRATE -->|Apply SQL and checksum history| PG
        PG --- DATA
        APP --- PROFILE
        PG -.->|Healthy database required| MIGRATE
        MIGRATE -.->|Successful completion required| APP
        MIGRATE -.->|Successful completion required| WORKER
    end
    APP --> EXT[Configured external providers]
    WORKER --> EXT
```

This deployment diagram is a **configuration view**, not a live server inspection. The Compose backend network is private; the application binds to loopback for the host proxy. App and worker startup depend on a healthy database and successful migration completion.

## Source basis

[`app/main.py`](../../app/main.py), [`app/application.py`](../../app/application.py), [`app/database.py`](../../app/database.py), [`app/web_sessions.py`](../../app/web_sessions.py), [`app/worker.py`](../../app/worker.py), [`app/routers/`](../../app/routers/), [`app/portals/`](../../app/portals/), [`app/repositories/`](../../app/repositories/), [`app/receipt_ocr.py`](../../app/receipt_ocr.py), [`app/emailer.py`](../../app/emailer.py), [`compose.prod.yml`](../../compose.prod.yml), [`deploy/nginx/meattrack.conf`](../../deploy/nginx/meattrack.conf). Provider confirmation: [[Database and Operations]].
