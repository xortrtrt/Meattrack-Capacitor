# MEATTRACK module flowcharts

Source review: **2026-10-31**, current local working tree through migration **044**. Modules below are logical business responsibilities. Several share the same portal or repository; a screen is not necessarily a separate module. Unless shown otherwise, actions require valid session, capability, assignment/ownership and input checks; failed validation returns an error without committing the requested transaction.

## Module map

| ID | Module | Main actors | Main implementation |
| --- | --- | --- | --- |
| M01 | Authentication and access | All portal users | `app/routers/auth.py`, `app/web_sessions.py`, `app/capabilities.py` |
| M02 | Public catalog and chatbot | Visitor | `app/routers/public.py`, `app/routers/chatbot.py`, `app/chatbot/` |
| M03 | Inquiry review and reseller onboarding | Visitor, Supervisor, Owner | `app/repositories/inquiries.py`, `app/repositories/accounts.py` |
| M04 | Reseller cart, payment and order fulfillment | Reseller, assigned Sales, Owner | `app/portals/reseller.py`, `app/repositories/orders.py` |
| M05 | Counter sales | Sales, Supervisor | `app/portals/team_leader.py`, `app/repositories/orders.py` |
| M06 | Materials, product definitions and production | Inventory, Owner | `app/repositories/inventory/`, `app/inventory_measurements.py` |
| M07 | Expiry, pull-outs and disposal | Inventory, Owner, worker | `app/worker.py`, `app/repositories/product_returns.py` |
| M08 | Product returns and version-1 settlement | Reseller, Sales, Supervisor, Inventory, Owner | `app/repositories/product_returns.py`, `app/repositories/return_customer.py` |
| M09 | Version-2 replacement requests | Reseller, Supervisor, Inventory, Owner | `app/repositories/replacement_workflow.py`, `app/repositories/product_returns.py` |
| M10 | Manual money refunds | Supervisor, Owner, Reseller | `app/repositories/refunds.py`, `app/repositories/refund_integrity.py` |
| M11 | Sales activity reporting and exports | Sales, Supervisor, Owner | `app/repositories/activity_reports.py`, `app/portals/activity_reports.py`, `app/report_documents.py` |
| M12 | Demand forecasting | Owner, worker | `app/repositories/forecasts.py`, `app/forecasting.py` |
| M13 | Public live chat | Visitor, available Sales Team Leader | `app/live_chat.py`, `app/routers/live_chat.py` |
| M14 | Private conversations and unread state | Authorized conversation participants | `app/reseller_messaging.py`, `app/repositories/replacement_messages.py`, `app/conversation_notifications.py` |
| M15 | Owner administration and handover | Owner | `app/portals/owner.py`, `app/repositories/superuser.py`, `app/repositories/accounts.py` |
| M16 | Background jobs and notifications | Worker, notification recipients | `app/worker.py`, `app/repositories/notifications.py` |
| M17 | Dashboards and operational monitoring | Each authorized role | `app/repositories/portal_dashboard.py`, `app/repositories/owner_insights.py`, `app/repositories/reporting.py` |
| M18 | Profile and account recovery | Account holder, authorized administrator | `app/repositories/accounts.py`, `app/portals/shared.py`, `app/owner_recovery.py` |

The four role-oriented summaries at the end show how these modules fit together in daily use.

## M01 — Authentication and access

```mermaid
flowchart TD
    A([Open login]) --> B[Enter email, password and consent]
    B --> C{Input, rate checks and credentials valid?}
    C -->|No| X[Show login error]
    X --> B
    C -->|Yes| D{Login OTP enabled?}
    D -->|Yes| E[Create OTP and send email synchronously]
    E --> F{Email sent and OTP confirmed?}
    F -->|No| X
    F -->|Yes| G[Record consent]
    D -->|No| G
    G --> H[Rotate database-backed session]
    H --> I[Redirect to role default workspace]
    I --> J{Requested action allowed by capability and scope?}
    J -->|No| K[Deny access]
    J -->|Yes| L[Execute authorized module action]
    L --> I
    I --> M[Logout or session expiration]
    M --> N([Session cleared or no longer valid])
```

Accounts must satisfy the authentication repository's active/activation checks. Sales Supervisor is stored as a specialized `team_leader` role, not a fourth `account_type`. Owner actions do not currently require a separate emailed step-up code.

## M02 — Public catalog and chatbot

```mermaid
flowchart TD
    A([Public visitor]) --> B{Choose activity}
    B -->|Browse| C[Load active finished products from database]
    C --> D[Render catalog or best sellers]
    B -->|Chatbot| E[Validate message and load catalog context]
    E --> F{Message intent}
    F -->|Reseller signup| G[Collect and validate lead fields]
    G --> H[Show summary and request submission confirmation]
    H --> I[Create inquiry through onboarding module]
    F -->|Human help| J[Offer live support handoff]
    J --> K[Collect display name and consent]
    K --> L[Start public live-chat module]
    F -->|Catalog or approved local answer| M[Build grounded local reply]
    F -->|Other supported question| N{Hosted provider configured?}
    N -->|No| M
    N -->|Yes| O[Request OpenRouter completion]
    O --> P{Reply available and grounded?}
    P -->|No| M
    P -->|Yes| Q[Return validated reply]
    M --> Q
    B -->|Partnership form| R[Validate public inquiry form]
    R --> I
```

Sources: `app/routers/public.py`, `app/routers/chatbot.py`, `app/chatbot/lead_flow.py`, `app/chatbot/provider.py`, `app/chatbot/catalog.py`.

## M03 — Inquiry review and reseller onboarding

```mermaid
flowchart TD
    A([Valid inquiry submitted]) --> B[Select active Sales Supervisor or Owner fallback]
    B --> C[Save assigned inquiry, activity and notification]
    C --> D[Supervisor or Owner reviews inquiry]
    D --> E{Approve?}
    E -->|No| F[Require public rejection explanation]
    F --> G[Save rejection and queue rejection email]
    E -->|Yes| H[Lock inquiry; validate email and required Supervisor assignment]
    H --> I[Create reseller profile and assigned Sales relationship]
    I --> J[Create pending reseller account and activation token]
    J --> K[Queue activation email in database outbox]
    K --> L[Worker delivers activation link]
    L --> M[Reseller sets and confirms password]
    M --> N{Token and password valid?}
    N -->|No| O[Show activation error or arrange resend]
    O --> M
    N -->|Yes| P[Consume token and activate account]
    P --> Q([Reseller can sign in])
    C -.->|Unresolved after one day| R[Worker queues inquiry follow-up email]
```

Approval and account creation are transactional; activation email delivery is asynchronous. An inquiry can notify Owner when no Supervisor is available, but approval through `add_reseller_from_inquiry` still needs a Supervisor assignment; Owner fallback does not remove that provisioning prerequisite. Supervisor and Owner account-management routes also support account provisioning outside a public inquiry.

## M04 — Reseller cart, payment and order fulfillment

```mermaid
flowchart TD
    A([Active assigned reseller]) --> B[Select active products and whole-pack quantities]
    B --> C[Add or update own cart]
    C --> D[Checkout selected items at server prices]
    D --> E[Create to_pay order with payment deadline; no stock hold]
    E --> F{Reseller action or deadline}
    F -->|Cancel while eligible and unpaid| G[Cancel order and record reason]
    F -->|Deadline passes| H[Expire unpaid order]
    F -->|Pay| I[Upload receipt, scan and confirm reference, time and amount]
    I --> J{Proof, deadline, amount and reference valid?}
    J -->|No| K[Show validation error]
    K --> I
    J -->|Yes| L[Lock order and check eligible stock atomically]
    L --> M{Enough stock?}
    M -->|No| N[Reject order; queue refund case requiring funds verification]
    N --> RF[Manual refund module]
    M -->|Yes| O[Store payment evidence; reserve all products; set pending]
    O --> P[Assigned Sales or Owner reviews]
    P --> Q{Approve or reject?}
    Q -->|Reject| R[Save reason, received-funds decision and clear holds]
    R --> S{Funds actually received?}
    S -->|Yes| RF
    S -->|No| T([Rejected without refund])
    Q -->|Approve| U[Set approved]
    U --> V[Authorized Sales or Owner fulfills using eligible FEFO batches]
    V --> W{Current stock and payment checks pass?}
    W -->|No| Y[Block fulfillment until resolved]
    Y --> V
    W -->|Yes| Z[Deduct batches, record allocations and movements; clear holds]
    Z --> AA[Set fulfilled and notify reseller]
    AA --> AB[Optional immutable order-received acknowledgement]
    AA --> AC[Eligible return or replacement module within 120 hours]
```

The state named `fulfilled` is shown as **Completed** in reseller history. Acknowledgement neither fulfills the order again nor restarts the return window. The payment deadline is configurable; it should not be assumed to be a universal fixed value. Approved orders have no ordinary reject/cancel path in this workflow. `order_stock_reservations` holds product quantities; batches are chosen again at fulfillment using FEFO.

## M05 — Counter sales

```mermaid
flowchart TD
    A([Sales or Supervisor records counter sale]) --> B[Choose product, whole packs and notes]
    B --> C[Validate active product and calculate server price]
    C --> D[Begin transaction; plan FEFO allocation under locks]
    D --> E{Enough eligible unreserved stock?}
    E -->|No| F[Return error and roll back]
    E -->|Yes| G[Create walk_in order and order lines]
    G --> H[Deduct batches and record allocations and sale movements]
    H --> I[Mark fulfilled in the same transaction]
    I --> J[Record actor activity and commit]
    J --> K([Sale available to dashboards, reports and forecasting])
```

The diagram groups atomic steps by business purpose. `create_order_from_items` inserts order records before allocation; allocation failure rolls back the transaction. Counter sales do not use the reseller `to_pay` stage.

## M06 — Materials, product definitions and production

```mermaid
flowchart TD
    A([Inventory or authorized Owner]) --> B{Operation}
    B -->|Raw material receipt| C[Validate material, positive quantity and supported unit]
    C --> D[Lock and increase material balance]
    D --> E[Append raw-receipt movement and activity]
    B -->|Product definition| F[Validate finished product, pack information and recipe components]
    F --> G[Save inventory item and product recipes]
    B -->|Production| H[Validate batch code, whole packs and dates]
    H --> I[Load recipe; lock raw material rows]
    I --> J[Calculate material requirements per produced pack]
    J --> K{Recipe, units and balances sufficient?}
    K -->|No| X[Reject and roll back]
    K -->|Yes| L[Create approved finished-product batch]
    L --> M[Deduct raw materials]
    M --> N[Append consumption and production-output movements]
    N --> O[Create expiry alert if near configured threshold]
    B -->|Owner price update| R[Validate price and update product catalog]
    E --> S([Updated inventory and audit history])
    G --> S
    O --> S
    R --> S
```

Raw materials and finished products share `inventory_items`, differentiated by `item_type`. Finished goods are tracked in batches and whole packs; raw materials support defined decimal-unit quantities. Production is a transaction, not a forecast-triggered background operation. A direct finished-batch receipt repository helper remains in `products.py`, but no current portal caller was found, so it is excluded from this active UI flow.

## M07 — Expiry, pull-outs and disposal

```mermaid
flowchart TD
    A([Request-time stock check or worker scan]) --> B{Batch expires on or before Manila business date?}
    B -->|No| C[Remain eligible if approved and available]
    B -->|Yes| D[Exclude batch from sellable availability immediately]
    D --> E[Worker marks expiry, synchronizes invalid replacement holds and queues pull-out]
    E --> F[Create or refresh alert and notify Inventory]
    F --> G[Inventory records physical pull-out quantity and remarks]
    G --> H[Deduct warehouse batch once and append pull-out movement]
    H --> I[Create non-sellable holding at Expired holding]
    J[Expired goods arriving from outside sellable stock] --> K[Record expired receipt into holding]
    R[Verified reseller return receipt] --> L[Record return holding at Discarded inventory]
    I --> M[Non-sellable holdings and custody ledger]
    K --> M
    L --> M
    M --> N[Inventory records disposal quantity, method and remarks]
    N --> O[Reduce holding balance and append disposal movement]
    O --> P([Preserved stock and custody trace])
```

Queueing a pull-out is a zero-physical-delta status event. Physical pull-out performs the warehouse deduction. Returned goods enter a separate custody ledger and never increase sellable stock. The table `expired_holdings` also stores returned goods despite its historical name.

## M08 — Product returns, refund-only requests and version-1 replacement cases

```mermaid
flowchart TD
    A([Reseller opens own fulfilled order]) --> B{Within 120 hours and traceable unclaimed allocations?}
    B -->|No| X[Block new request and explain reason]
    B -->|Yes| C[Select returned packs, outcome, reason and 1-3 photos]
    C --> D{New replacement with selected products?}
    D -->|Yes| V2[Use version-2 replacement module M09]
    D -->|No: refund-only or existing version-1 case| E[Save submitted case linked to original allocations]
    E --> F[Assigned Sales reviews new request]
    F --> G{Accept?}
    G -->|No| H[Reject with reseller-visible explanation]
    G -->|Yes| I[Record approval and hand off to Supervisor or Owner]
    I --> J[Inventory compares actual goods to current approved request]
    J --> K{Complete match?}
    K -->|No| L[Record discrepancy; block settlement]
    L --> M[Supervisor or Owner resolves mismatch and revises within claim bounds]
    M --> J
    K -->|Yes| N[Atomically record complete non-sellable custody and verification]
    N --> O[Clear recorded approval for settlement]
    O --> P{Resolution}
    P -->|Refund| Q[Create or reconcile refund obligation]
    Q --> RF[Manual refund module M10]
    P -->|Version-1 replacement| R[Reseller accepts current quote; Inventory reserves eligible FEFO stock]
    R --> S{Higher replacement value?}
    S -->|Yes| T[Upload top-up proof; authorized processor verifies exact collection]
    S -->|No| U[Inventory releases replacement]
    T --> U
    U --> W{Lower replacement value?}
    W -->|Yes| RF
    W -->|No| Y[Check completion requirements]
    RF --> Y
    Y --> Z([Complete case when custody and required settlement reconcile])
    Z --> AA[Separate reseller acknowledgement or reported follow-up]
```

Normal complete-match receipt authorizes the existing approval in the Inventory transaction; older cases can still require historical/manual reconciliation. Regular Sales becomes tracking-only after handoff. The original order and fulfilled-sale allocation remain intact.

## M09 — Version-2 replacement workflow

```mermaid
flowchart TD
    A([Reseller selects eligible original packs and photos]) --> B[Choose replacement products and whole-pack quantities]
    B --> C[Server calculates original-price return credit and replacement pricing components]
    C --> D[Review signed account-bound comparison]
    D --> E[Revalidate and submit immutable comparison revision]
    E --> F[Assign Supervisor directly, with Owner fallback; create private case conversation]
    F --> G{Supervisor approves?}
    G -->|No| H[Decline with public explanation; case chat becomes read-only]
    G -->|Change before paid or released| I[Publish revised comparison and require reseller acceptance]
    I --> G
    G -->|Yes| J[Notify Inventory for complete receipt]
    J --> K{Actual goods fully match current approval?}
    K -->|No| L[Record mismatch; Supervisor resolves or revises comparison]
    L --> I
    K -->|Yes| M[Record non-sellable custody, verification and approval clearance]
    M --> N{Current comparison accepted?}
    N -->|No| O[Wait for reseller acceptance]
    O --> N
    N -->|Yes| P{Replacement value exceeds return credit?}
    P -->|Yes| Q[Reseller submits exact top-up with scanned and confirmed receipt]
    Q --> R[Supervisor confirms actual received funds and matching reference]
    P -->|No| S[Inventory prepares eligible FEFO stock]
    R --> S
    S --> T{Valid stock available?}
    T -->|No| U[Wait for valid restock; preserve selection and verified payment]
    U --> S
    T -->|Yes| V[Reserve batches, mark ready and notify processor; no physical deduction]
    V --> W[Inventory revalidates and confirms warehouse release]
    W --> X{Stock, expiry, reservation and financial checks pass?}
    X -->|No| U
    X -->|Yes| Y[Deduct stock once; append release and movement; clear holds]
    Y --> Z[Supervisor or Owner records delivery]
    Y --> AA{Replacement cheaper than credit?}
    AA -->|Yes| AB[Complete difference refund through M10]
    AA -->|No| AC[No difference refund required]
    Z --> AD{Delivery and all required settlement complete?}
    AB --> AD
    AC --> AD
    AD -->|No| AE[Keep case awaiting remaining work]
    AE --> AD
    AD -->|Yes| AF([Complete replacement case])
```

Credit covers only selected returned packs at original paid prices. Matching same-product quantities retain those prices; excess or different products use catalog pricing captured in the comparison. Version 2 collects any top-up **before preparation**. Preparation and release are separate actions. Delivery and any required refund must both complete before the case completes.

## M10 — Manual money refunds

```mermaid
flowchart TD
    A([Refund obligation from rejected paid order or verified return]) --> B[Open assigned refund as Supervisor or Owner]
    B --> C{Payment review still required?}
    C -->|Yes| D[Verify actual receipt of original funds]
    D --> E[Recheck financial and return-settlement eligibility]
    C -->|No| E
    E --> F{Eligible and not already completed?}
    F -->|No| X[Block action and explain unresolved condition]
    F -->|Yes| G[Claim or start numbered processing attempt under lock]
    G --> H[Processor manually transfers exact refund using external provider]
    H --> I{Transfer successful?}
    I -->|No| J[Record failed attempt and reason]
    J --> G
    I -->|Yes| K[Enter method, reference, transfer time and remarks; attach proof]
    K --> L[Validate exact amount, uniqueness, evidence and current processor]
    L --> M{Validation passes?}
    M -->|No| N[Keep processing attempt; correct details]
    N --> K
    M -->|Yes| O[Complete attempt and refund atomically]
    O --> P[Recheck linked return completion and publish system messages]
    P --> Q([Reseller can review completed refund])
    G -.->|Owner takeover when necessary| R[Preserve prior attempt history and record takeover]
    R --> G
```

The application records the manual transfer; it does not initiate the transfer. Refund screenshots alone do not establish original collection. Failed attempts remain recorded, and operation keys protect supported retries.

## M11 — Sales activity reporting and exports

```mermaid
flowchart TD
    A([Sales or Supervisor chooses report period]) --> B[Validate period using Manila dates]
    B --> C[Select orders by original order creation date]
    C --> D[Require current access and recorded personal qualifying actions]
    D --> E[Build Orders, fulfilled Sales, Returns and Refunds sections]
    E --> F[Aggregate money once per order and add optional notes]
    F --> G[Display account-bound signed preview]
    G --> H[Submit with unique submission key]
    H --> I[Rebuild data in a consistent transaction]
    I --> J{Matches reviewed preview?}
    J -->|No| K[Reject stale preview and retain notes for review]
    K --> G
    J -->|Yes| L[Insert sales report and immutable activity document snapshot]
    L --> M[Save traceability membership and notify Owner]
    M --> N[Author or Owner opens saved document]
    N --> O[Render print view, section CSV or five-sheet Excel workbook]
    O --> P([Submitted values remain frozen])
```

Assignment alone is insufficient for report attribution. Replacement releases and top-ups are not new ordinary sales. Only completed refunds against fulfilled sales reduce retained sales. New reports use saved JSON documents; legacy report behavior remains supported. Reseller sell-through repository helpers and schema remain, but the inspected current reseller portal does not expose an active submission route; they are not shown as an active module here.

## M12 — Demand forecasting

```mermaid
flowchart TD
    A([Owner requests forecast with horizon]) --> B[Validate request and persist queued run]
    B --> C[Worker atomically claims run]
    C --> D[Load active products, fulfilled-sales history and usable stock]
    D --> E[Construct daily series per product]
    E --> F{Enough history and nonzero demand?}
    F -->|No| G[Store insufficient-history diagnostic and zero estimates]
    F -->|Yes| H[Backtest eligible Prophet, seasonal naive, moving average and TSB candidates]
    H --> I[Choose model using backtest scores or eligible fallback]
    I --> J{Candidate fit succeeds?}
    J -->|No| K[Try next candidate or record failed diagnostic]
    K --> L[Collect product output]
    J -->|Yes| M[Predict daily demand and bounds where supported]
    M --> N[Compute forecast total and production gap against usable stock]
    N --> L
    G --> L
    L --> O{More products and no cancellation?}
    O -->|More products| E
    O -->|Cancellation| P[Mark run cancelled]
    O -->|Finished| Q[Persist summaries and daily results atomically]
    Q --> R[Mark completed or completed_with_warnings; notify Owner]
    R --> S([Owner reviews forecast and makes production decisions])
    C -.->|Run-level failure| T[Record error; retry or fail according to run policy]
```

This is an implemented model-selection pipeline, not a guarantee that Prophet is always selected. Forecasting uses local Python libraries. An insufficient-history zero is diagnostic output, not evidence of zero future demand. `production_gap` is forecast total minus usable stock, so a negative value is possible.

## M13 — Public live chat

```mermaid
flowchart TD
    A([Visitor accepts live support and data consent]) --> B[Create queued conversation and bind visitor access]
    B --> C[Notify available Sales Team Leaders]
    C --> D{Agent claims before timeout?}
    D -->|No| E[Move to awaiting_contact]
    E --> F[Validate fallback contact and mark follow_up]
    D -->|Yes| G[Lock and assign conversation; mark active]
    G --> H[Validate participant message and save it in PostgreSQL]
    H --> I[Publish event through Ably]
    I --> J{Published?}
    J -->|No| K[Record publication failure for worker retry]
    K --> L[Participants retain persisted message access]
    J -->|Yes| L
    L --> M{Next action}
    M -->|Continue| H
    M -->|Transfer| B
    M -->|Request reseller form| N[Submit inquiry linked to this conversation]
    N --> H
    M -->|Close or cancel| O[Record closed or cancelled state]
    O --> P[Allow eligible post-chat service rating]
    P --> Q([Retain or clean transcript under retention policy])
```

Public live chat has its own tables, visitor authorization and Ably publication retry state. It is distinct from internal reseller conversations and replacement case messages. Current availability and claim queries require `team_leader_role='sales'`; Supervisor navigation/capabilities alone do not establish eligibility to claim public chats.

## M14 — Private conversations and unread state

```mermaid
flowchart TD
    A([User opens Inbox or staff Messages]) --> B[Load only currently authorized threads]
    B --> C{Conversation type}
    C -->|Sales and reseller| D[Check assigned leader and reseller participants]
    C -->|Refund| E[Check processor or reseller permissions and refund status]
    C -->|Version-2 replacement| F[Check case reseller, current Supervisor or Owner]
    D --> G[Load persisted messages and current read cursor]
    E --> G
    F --> G
    G --> H{Send reply?}
    H -->|Yes| I[Validate text, client UUID and allowed media]
    I --> J[Recheck assignment and case state; persist or return matching retry]
    J --> K[Refresh visible thread and unread summary]
    H -->|No| K
    K --> L{Actual messages displayed in visible thread?}
    L -->|Yes| M[Advance read cursor only to displayed messages]
    L -->|No| N[Leave hidden messages unread]
    M --> O[Poll active thread and update conversation badges]
    N --> O
    O --> H
```

General reseller conversations support private image/video attachments; version-2 case chat is text-based, with evidence and payment in linked return details. Refund conversations close to new replies when completed; declined replacement cases are read-only. Private media downloads recheck authorization. The replacement thread polls while visible; it does not require a new realtime provider.

## M15 — Owner administration and handover

```mermaid
flowchart TD
    A([Authenticated Owner]) --> B[Open accounts, operations, inventory, alerts or settings]
    B --> C[Choose authorized action and provide required reason]
    C --> D{Action type}
    D -->|Account status or role| E[Inspect retained Owner access and outstanding workload]
    E --> F{Safe under current assignment and processing rules?}
    F -->|No| G[Require workload transfer or completion of active attempts]
    G --> E
    F -->|Yes| H[Apply status or role change and applicable session revocation]
    D -->|Staff handover| I[Validate active destination and Supervisor capability for money duties]
    I --> J[Transfer assignments and open processing responsibility]
    D -->|Inventory correction| K[Lock balance and append compensating movement]
    D -->|Settings or pricing| L[Validate values and save changes]
    D -->|Recovery or operations| M[Revoke sessions, queue password reset, manage alerts or forecast runs]
    H --> N[Record required audit evidence]
    J --> N
    K --> N
    L --> N
    M --> N
    N --> O([Updated workspace and preserved history])
```

Handover respects current return/refund processing duties and private conversation scope. In-progress refund transfers require their existing completion, failure or Owner takeover flow. Corrections append movements rather than editing immutable stock history. No additional Owner step-up email challenge is required in the current portal.

## M16 — Background jobs and notifications

```mermaid
flowchart TD
    A([Worker loop]) --> B{Maintenance interval due?}
    B -->|Yes| C[Queue due inquiry follow-ups and scan batch expiry]
    C --> D[Clean sessions and old security data; maintain chat]
    D --> E[Recover stale forecast runs and write system status]
    B -->|No| F[Expire unpaid orders]
    E --> F
    F --> G[Claim due email outbox item under lock]
    G --> H{Item available?}
    H -->|Yes| I[Send activation, reset, rejection or inquiry-update email]
    I --> J{Delivery succeeds?}
    J -->|Yes| K[Mark sent and release lock]
    J -->|No| L[Record error, attempt count and exponential retry time]
    K --> G
    L --> G
    H -->|No| M[Claim and process one forecast run]
    M --> N[Retry failed public live-chat publications]
    N --> O[Pause until next fast cycle]
    O --> A
    P[Business action] --> Q[Create in-portal notification and recipient state]
    Q --> R[Authorized user reads or opens notification]
    R --> S[Persist recipient read state]
```

The current worker targets a five-second fast cycle and maintenance about every sixty seconds. A slow synchronous task can lengthen the loop. Email outbox claims stop after the configured code limit of eight delivery attempts; failed delivery is not silently treated as success. Login and password-change OTP emails use request-time sending rather than this outbox.

## M17 — Dashboards and operational monitoring

```mermaid
flowchart TD
    A([Authorized user opens dashboard]) --> B[Resolve role and current assignment scope]
    B --> C{Portal view}
    C -->|Owner| D[Aggregate business sales, collections, obligations, stock and work queues]
    C -->|Sales or Supervisor| E[Aggregate assigned orders, reseller work and personal or team activity]
    C -->|Inventory| F[Aggregate products, batches, availability, movements and expiry]
    C -->|Reseller| G[Aggregate own orders, cart and inbox state]
    D --> H[Apply requested dates, search, filters and pagination]
    E --> H
    F --> H
    G --> H
    H --> I[Render Jinja2 workspace and chart data]
    I --> J[Open linked order, stock, return, report or account module]
    J --> K([Target module enforces its own action permissions])
```

Physical stock, sellable stock, reserved stock, gross sales, retained sales and net collections have different meanings. Dashboard totals do not replace the transaction and settlement checks.

## M18 — Profile and account recovery

```mermaid
flowchart TD
    A([Account holder or authorized administrator]) --> B{Requested operation}
    B -->|Profile details| C[Validate own editable fields and update profile]
    B -->|Profile photo| D[Validate image, save file and crop settings]
    B -->|Change own password| E[Validate current password and new password]
    E --> F[Create password-change OTP and send email]
    F --> G{Code valid and unexpired?}
    G -->|No| H[Reject confirmation]
    G -->|Yes| I[Apply password update and session handling]
    B -->|Administrator-forced reset| J[Authorize managed account and required reason]
    J --> K[Queue password-reset activation link]
    K --> L[Account holder validates token and sets password]
    B -->|Resend activation| M[Create or refresh allowed activation and queue delivery]
    C --> N([Saved result and relevant activity history])
    D --> N
    I --> N
    L --> N
    M --> N
```

Legacy Owner form-draft recovery helpers remain in `app/owner_recovery.py`; their presence does not imply a public self-service password-reset endpoint. Profile photos live in the upload volume, while account references and crop settings live in PostgreSQL.

## Role-oriented portal summary: Owner

```mermaid
flowchart TD
    A([Owner sign-in]) --> B[Overview and operational work queues]
    B --> C{Work area}
    C --> D[Accounts, reseller assignments and staff handover]
    C --> E[Orders and exceptional order decisions]
    C --> F[Inventory, materials, prices, corrections and expiry]
    C --> G[Returns, replacements and manual refunds]
    C --> H[Submitted report inbox and exports]
    C --> I[Forecast queue and result review]
    C --> J[Operations, alerts, recovery and settings]
    D --> B
    E --> B
    F --> B
    G --> B
    H --> B
    I --> B
    J --> B
```

## Role-oriented portal summary: Sales and Supervisor

```mermaid
flowchart TD
    A([Sales or Supervisor sign-in]) --> B[Scoped dashboard]
    B --> C[Assigned order review and fulfillment]
    B --> D[Counter sales and personally attributed reports]
    B --> E[Assigned reseller conversations]
    B --> F{Specialized role}
    F -->|Sales| G[Initial version-1 return decision and assigned-case tracking]
    F -->|Sales| L[Available-agent public live support]
    G --> H[Accepted cases handed off; no further money processing]
    F -->|Supervisor| I[Inquiry review and permitted account management]
    F -->|Supervisor| J[Assigned replacement approval, top-up verification and delivery]
    F -->|Supervisor| K[Assigned manual refund attempts and settlement]
```

## Role-oriented portal summary: Inventory

```mermaid
flowchart TD
    A([Inventory sign-in]) --> B[Stock and expiry dashboard]
    B --> C[Receive materials and maintain product recipes]
    C --> D[Produce and record finished batches]
    B --> E[Inspect complete return receipt or report mismatch]
    E --> F[Record verified non-sellable custody]
    F --> G[Prepare financially cleared replacement stock]
    G --> H[Mark ready, then separately release warehouse goods]
    B --> I[Physically pull out expired stock]
    I --> J[Record holding and later disposal]
    B --> K[Inspect batch history and immutable movement logs]
```

## Role-oriented portal summary: Reseller

```mermaid
flowchart TD
    A([Activated reseller sign-in]) --> B[Browse products and manage own cart]
    B --> C[Checkout to unpaid order]
    C --> D[Submit receipt and confirmed payment details before deadline]
    D --> E[Track Sales review and fulfillment]
    E --> F[Optionally acknowledge order received]
    E --> G{After-sales request within 120 hours?}
    G -->|Refund| H[Submit allocation quantities, reason and photos]
    G -->|Replacement| I[Choose products and submit reviewed comparison]
    H --> J[Track decision, custody and settlement]
    I --> J
    J --> K[Accept revisions, pay top-up when due or review refund]
    K --> L[Review replacement delivery and completion]
    E --> M[Read order updates or message authorized staff]
    L --> M
```

## Source verification boundaries

These are source-derived flows, not results from a fresh end-to-end test run. Relevant existing regression files include `tests/test_portal_improvements.py`, `tests/test_inventory_receipt_decision.py`, `tests/test_return_workflow_handoff.py`, `tests/test_replacement_workflow.py`, `tests/test_refund_traceability.py`, `tests/test_activity_reports.py`, `tests/test_forecasting.py`, `tests/test_live_chat.py`, and `tests/test_internal_conversation_media.py`. No passing-test or deployment claim is made here.
