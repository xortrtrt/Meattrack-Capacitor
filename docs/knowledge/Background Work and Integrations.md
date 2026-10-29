---
tags: [meattrack, worker, integrations]
verified: 2026-10-28
---
# Background work and integrations

`app/worker.py` runs separately from HTTP requests. Its fast cycle expires unpaid
orders, drains the notification outbox, processes a forecast run, and retries
live-chat publication. Maintenance handles inquiry follow-ups, batch expiry,
session/security cleanup, chat maintenance, and stale forecast recovery.

Batch-expiry maintenance creates one durable Pending pull-out record per batch.
Inventory Logs reads that same record as a status event without changing stock;
only the confirmed physical pull-out deducts warehouse quantities. See
[[Orders and Inventory]]; checked 2026-10-29 against `app/worker.py`,
`app/repositories/product_returns.py`, and `app/repositories/inventory/queries.py`.

| Feature | Persistent/local component | External relationship |
| --- | --- | --- |
| Notifications/email | Database outbox and worker delivery | Brevo; local mail capture in development |
| Demand forecasts | Durable runs, per-product summaries, daily results | Local Prophet/pandas and baseline models |
| FAQ chatbot | Approved local FAQ and guided lead collection | Optional OpenRouter provider |
| Live prospect chat | Conversations, messages, presence and retry state | Ably publishes browser events |
| Reseller messaging | Order/reseller-scoped persisted conversations | Realtime behavior must preserve ownership |

The optional hosted chatbot and the live support workflow have different failure
paths. The FAQ has a local fallback. Chat publication failures use persistent retry
state; do not assume successful external publication merely because a message is
stored. Inspect feature configuration before diagnosing a missing integration.

Forecast request and forecast computation are separate operations. An owner-facing
run/status action connects to a worker claim and processing step. Sales history
and inventory availability connect forecasts to [[Orders and Inventory]]; model
output is not evidence of production stock or financial settlement.

Keep provider keys out of notes and browser code. See [[Roles and Access]] for
scoping and [[Database and Operations]] for starting both app and worker.

Sources: [worker](../../app/worker.py), [forecast repository](../../app/repositories/forecasts.py),
[forecast models](../../app/forecasting.py), [email](../../app/emailer.py),
[chat provider](../../app/chatbot/provider.py), [live chat](../../app/live_chat.py),
[reseller messaging](../../app/reseller_messaging.py).
