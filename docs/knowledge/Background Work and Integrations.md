---
tags: [meattrack, worker, integrations]
verified: 2026-11-04
---
# Background work and integrations

## Reseller opportunities and invitation email (2026-11-03)

The landing chatbot's **How to Become a Reseller?** option displays the designated
Business Owner email (`BUSINESS_EMAIL`) and collects no application details.
Negotiation and approval happen externally. An active Owner can issue a unique
seven-day registration invitation from People & access; the recipient email is
sent through the existing Brevo emailer. The raw token is sent directly and is
not written to the database; PostgreSQL stores only its SHA-256 hash. Registration
uses the existing email OTP sender and `account_login_otps` table with a distinct
`reseller_registration` purpose, ten-minute expiry, 60-second resend cooldown,
up to 50 requests per account per hour in local Compose (five by default
elsewhere), and at most five attempts per code. See [[Owner Portal]] and
[[Roles and Access]].

Login email OTP uses the same sender and OTP table with its own three-minute
resend cooldown (up to 50 requests per hour in local Compose, five by default
elsewhere) and a resend action on the pending login page. Local Compose
configures the Brevo sender as `noreply@batangaspremium.com` and sends through
Brevo using `BREVO_API_KEY` from `.env`; the API URL defaults to Brevo's
transactional endpoint. The local mail-capture service has been removed. Without
a valid API key, delivery fails visibly and the login OTP is not falsely
reported as sent. Sources:
`app/routers/auth.py`, `app/security_controls.py`,
`app/repositories/accounts.py`, `app/config.py`, `app/emailer.py`, and
`compose.yml`.

Public visitor-to-Sales Live Chat was retired on 2026-11-03. The chatbot still
answers product and delivery questions and gives the public phone/email when a
visitor asks for a person; it no longer offers or starts a Sales handoff. The
Sales and Supervisor Live Chat page and its visitor conversation APIs are gone.
Existing internal reseller messages remain available to signed-in accounts,
and historical prospect-chat rows keep their normal retention cleanup. Sources:
`app/chatbot/lead_flow.py`, `app/routers/chatbot.py`,
`app/routers/live_chat.py`, `app/portal_config.py`, and `app/worker.py`.

Sources: `app/chatbot/lead_flow.py`, `app/chatbot/configuration.py`,
`app/routers/chatbot.py`, `app/routers/live_chat.py`, `app/emailer.py`,
`app/repositories/reseller_invitations.py`, `app/repositories/accounts.py`,
`app/security_controls.py`, migration
`database/migrations/050_reseller_registration_invitations.sql`.

## Privacy notice service disclosures (2026-11-03)

The public chatbot keeps the six most recent chat messages, each limited to 300
characters, in the MEATTRACK session; when the hosted answer path is used, the
current message and that history are sent to the configured OpenRouter-compatible
model endpoint.
Transactional account email is sent through the configured Brevo API. Messages in
historical visitor live-chat conversations are deleted by the retention job after
the configured `CHAT_TRANSCRIPT_RETENTION_DAYS` period (30 days by default) when
the conversation is closed or cancelled; this does not define retention for
business orders, return evidence, or authenticated reseller conversations. These
service and retention statements are reflected in the public privacy notice
(`app/templates/privacy.html`). Sources checked 2026-11-03:
`app/chatbot/provider.py`, `app/chatbot/lead_flow.py`, `app/emailer.py`,
`app/live_chat.py`, and `app/config.py`.

Owner account status changes insert a durable `account_deactivated` or
`account_reactivated` email event
in the same transaction as the status change and audit record. The web request
attempts delivery through Brevo immediately and marks the outbox event complete
only when the provider accepts the message. A failed attempt is saved with an
error and retried by the worker; the Owner sees that delivery did not succeed.
Reactivation sends a restored-access notice containing the reason and explaining
that the account holder can sign in using their existing password. Sources:
`app/repositories/superuser.py`, `app/portals/owner.py`, `app/worker.py`, and
`app/emailer.py`. Checked 2026-11-03. See [[Owner Portal]] and [[Roles and Access]].

## Reseller unified inbox (2026-11-02)

### Sales workspace categories (2026-11-04)

The Sales Supervisor Messages page presents direct reseller messages and
replacement case conversations in one workspace, separated by accessible
Reseller messages and Replacement conversations tabs. Both category views use a
conversation rail and reading pane; the replacement pane stays in an instructional
empty state until a case is selected. A replacement deep link opens the replacement
category; direct messaging and case messaging retain their separate APIs,
permissions, history, and unread behavior. Team Leader direct messaging remains
unchanged because that role does not have private replacement case access. This is
a presentation change only. Sources checked:
`app/templates/portals/team-leader/reseller_messages.html`,
`app/templates/portals/_replacement_conversation.html`,
`app/templates/portals/_replacement_conversation_links.html`,
`app/static/js/sales_reseller_messages.js`, and
`app/static/js/replacement_workflow.js`. See [[Replacement Workflow]].

### Participant grouping refinement

The reseller list now uses compact All/Direct/Replacements/System tabs under
search, one list scrollbar, and activity ordering. Multiple replacement histories
with the same current processor appear under one contact. The aggregate contact
read reauthorizes each case and keeps source message IDs and per-case read cursors.
If the current direct-chat leader is that same account, direct history joins that
contact; a different Sales account remains separate. Staff case views retain their
existing case-specific permissions. No history is copied, deleted, or reassigned.

Direct messages can carry an optional owned `order_id` via migration 047. The
backend checks order ownership and retry context. The reseller can select an order
to display a read-only inquiry card above the composer. Regarding defaults to
**No order selected**, resets after a successful send, and does not filter history.
Order references appear as centered, system-style context rows between messages,
rather than badges attached above a sender's bubble. They are presentation of the
existing message association, not additional stored system notifications. Clicking opens
the scoped preview without selecting an order for the next message or changing
conversation/history. Regarding and replacement selectors now share the bottom
composer area with the card; this follows the later full refinement brief.
Private case-chat context rows use the actual **Replacement #return_id** rather
than the original order number. Clicking expands that case's existing authorized
comparison without changing the composer case selection. Direct order inquiries
retain their order references.
Cards start collapsed with products, status, total, details link, and dismiss;
native expansion shows the existing date, item, and receipt/fulfillment facts.
Consecutive same-sender messages within five minutes are visually grouped while
their original rows and timestamps remain intact. Direct refresh preserves an
older reading position. Existing Delete actions now live in a native overflow
menu and still use the same confirmation and soft-hide endpoints. Desktop omits
the mobile Back controls. System notification entries appear exclusively in the System
tab, including during search; All counts exclude those order-update threads.
Replacement replies
still target an explicit case; declined cases remain read-only. General/private
media replies are available only through an existing authorized direct relationship.
Selecting a contact does not navigate away from Inbox. Case details stay linked.
Order History's inquiry action opens the existing chat with explicit order context.
The card endpoint checks `created_by_account_id` before returning products, quantities,
status, date, total, and actual receipt/fulfillment facts. No payment evidence is
returned. Source: `app/static/js/order_inquiry_cards.js`,
`app/reseller_contact_history.py`, and `app/templates/portals/reseller/history.html`.

Sources checked: `app/reseller_contact_history.py`, `app/reseller_messaging.py`,
`app/routers/live_chat.py`, `app/conversation_notifications.py`,
`app/static/js/reseller_contact_history.js`, `app/static/js/reseller_inbox.js`,
`app/templates/portals/reseller/_contact_history.html`, migration
`047_message_order_context.sql`. This section supersedes the earlier per-case
reseller list description below. See [[Roles and Access]] and [[Replacement Workflow]].

The reseller Inbox uses one conversation rail with All Messages, Direct Messages,
Replacements, and System Messages filters and local search by participant, preview,
order, or return number. Replacement links retain `?replacement=ID` and the existing
case-specific message/read endpoints; they no longer replace the entire Inbox.
Direct messages and order/refund conversations retain their existing handlers and
identifiers. Mobile shows the list first unless a conversation is deep-linked,
then offers a Back control. Switching types hides the previous message log.

The selected replacement shows its existing status and a collapsible price
comparison from the authorized `get_product_return` read. Payment actions and
supporting evidence remain in the linked return details. No presence provider is
available: the direct-chat indicator describes messaging readiness, not online
status. Search covers records loaded into the existing workspace; it is not an
archive search. See [[Replacement Workflow]] and [[Testing and Change Impact]].

Sources checked: `app/templates/portals/reseller/inbox.html`,
`app/templates/portals/reseller/_inbox_replacement.html`, `app/portals/reseller.py`,
`app/repositories/replacement_messages.py`, `app/static/js/reseller_inbox.js`,
`app/static/js/replacement_workflow.js`, `app/static/css/reseller_inbox_workspace.css`.

`app/worker.py` runs separately from HTTP requests. Its fast cycle expires unpaid
orders, drains the notification outbox, and processes a forecast run. It no
longer retries visitor chat publications. Maintenance handles batch expiry,
session/security cleanup, historical chat retention, and stale forecast recovery.

Batch-expiry maintenance creates one durable Pending pull-out record per batch.
Inventory Logs reads that same record as a status event without changing stock;
only the confirmed physical pull-out deducts warehouse quantities. See
[[Orders and Inventory]]; checked 2026-10-29 against `app/worker.py`,
`app/repositories/product_returns.py`, and `app/repositories/inventory/queries.py`.

| Feature | Persistent/local component | External relationship |
| --- | --- | --- |
| Notifications/email | Database outbox and worker delivery | Brevo in local and production environments |
| Demand forecasts | Durable runs, per-product summaries, daily results | Local Prophet/pandas and baseline models |
| FAQ chatbot | Approved local FAQ and Owner contact details | Optional OpenRouter provider |
| Historical prospect chat | Retained records and cleanup only | No new Ably publication |
| Reseller messaging | Order/reseller-scoped persisted conversations | Realtime behavior must preserve ownership |

The optional hosted chatbot has a local fallback. Visitor-to-Sales live support
is retired; internal reseller messaging remains a separate authenticated workflow.

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
return context, and attachment identities. The legacy
`/api/portal/live-chat/resellers/...` paths serve authenticated internal
conversations; the public visitor Live Chat routes are removed.

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

## Stock alert episodes (checked 2026-11-01)

After expiry/custody maintenance, `scan_batch_expiry` calls stock-alert reconciliation.
It uses a shared database availability view, the saved low-stock policy, transactional
upserts, and an advisory lock to create or clear episodes. Receipt/production uses
the same batch-alert helper, so the next scan does not duplicate notifications.
Expiry and low-stock notifications deduplicate by alert ID, role, and severity;
Owner notifications remain critical-only and respect the Owner setting.
Automatic closure writes its timestamp, source, reason, and system audit together.
The successful scan timestamp is committed in that same reconciliation transaction.
A failed scan leaves the previous timestamp visible. No request-time GET performs
maintenance. See [[Owner Portal]] and [[Orders and Inventory]]. Sources:
`app/worker.py`, `app/repositories/stock_alerts.py`, migration 045.

Inventory stock notifications now open `/portal/team-leader/alerts#stock-alert-ID`.
Existing alert-source notification URLs are normalized for Inventory on display and
open. Receipt/production and worker paths retain the same episode deduplication;
adding the Inventory view creates no separate lifecycle or notification stream.
Checked 2026-11-01 against `app/repositories/stock_alerts.py` and
`app/portals/shared.py`. See [[Owner Portal]] and [[Orders and Inventory]].

## Profile password OTP before password entry

All profile roles send password verification OTPs immediately through the shared
Brevo emailer. A failed send cancels the challenge and renders an actionable
profile error. Successful provider acceptance advances the profile to OTP entry;
it does not prove inbox delivery. The user verifies the code before entering a
new password. Password challenge retention is 30 days after expiry. Sources:
`app/portals/password_change.py`, `app/repositories/password_changes.py`,
`app/emailer.py`, and `app/worker.py`. See [[Roles and Access]] and [[Architecture]].
