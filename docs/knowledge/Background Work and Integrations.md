---
tags: [meattrack, worker, integrations]
verified: 2026-10-30
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

## Internal conversation media and unread state

Assigned leader–reseller conversations accept up to three private attachments:
JPG/PNG/WebP images up to 5 MB, MP4/WebM videos up to 25 MB, and 30 MB combined.
The server checks content, stores bytes with the message, and serves authenticated
participant-only media with video range requests. Deleted-thread visibility and
assignment checks also apply to downloads. Message UUID retries must match text,
return context, and attachment identities. Public Live Chat is unchanged; the
legacy `/api/portal/live-chat/resellers/...` paths serve internal conversations.

Navigation badges count unread conversations; rows show incoming unread messages.
The reseller count folds system updates and refund replies into one thread per
order. Refund read cursors live separately from append-only messages. Clients mark
only displayed message IDs read while the thread/document is visible. The unread
summary polls every 15 seconds and refreshes on focus; active messages retain their
existing three-second refresh. Reading a list does not clear hidden conversations.

Sources checked 2026-10-29: `app/reseller_messaging.py`,
`app/internal_conversation_media.py`, `app/conversation_notifications.py`,
`app/static/js/internal_conversations.js`, `app/static/js/refunds.js`, migration 042.
See [[Roles and Access]] and [[Returns and Financials]].

Internal conversation drafts have a separate X control on each attachment.
Received media opens in a native modal with Escape/close support and video
controls. Enter sends; Shift+Enter adds a line, and IME composition is preserved.
Attachment-only messages omit empty text bubbles. Participant headers and lists
use account profile photos and crop settings, with a generic user icon fallback.
Sales opens on an explicit conversation-selection screen. Refund conversations
use a separate message scroll area and spaced reply composer. These presentation
changes do not alter Public Live Chat or private media authorization. Checked
2026-10-30 against `app/static/js/internal_conversations.js`,
`app/static/js/sales_reseller_messages.js`, `app/static/js/reseller_inbox.js`,
`app/static/css/internal_conversations.css`, `app/reseller_messaging.py`,
`app/repositories/refunds.py`, and the corresponding inbox templates.

[[Replacement Workflow]] adds private case conversations within existing Inbox/
Reseller Messages workspaces. Persistent text, three-second visible polling,
conversation unread counts, displayed-message read cursors, and current assignment
checks are independent of the general Sales conversation and public live chat.
Supporting media/payment evidence stays in linked return details. No additional
realtime provider is required. Sources: `app/repositories/replacement_messages.py`,
`app/conversation_notifications.py`, `app/static/js/replacement_workflow.js`.

The replacement messenger now shares the Sales/reseller list, participant photo,
message bubble, and composer presentation. Desktop uses a list/thread split;
mobile switches between them without marking the hidden thread read. Latest
message previews/order come from the existing case-message table, not an external
chat provider. This 2026-10-31 presentation update adds no schema migration or
worker job. Sources: `app/templates/portals/_replacement_conversation.html`,
`app/templates/portals/_replacement_thread_rows.html`,
`app/static/css/replacement_workflow.css`,
`app/static/js/replacement_workflow.js`, `app/repositories/replacement_messages.py`.
See [[Replacement Workflow]] and [[Testing and Change Impact]].
