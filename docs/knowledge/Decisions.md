---
tags: [meattrack, decisions]
verified: 2026-11-03
---
# Decisions

## 2026-11-03 — Align the local database with the restored 049 application

The user explicitly requested return to migration 049 after restoring commit
`dd21778`. Reverse only migration 050's unused GCash schema and its migration
record, preserving existing business data rather than restoring an older data
snapshot. Verify a private backup restore, rehearse the reversal on a disposable
database, and compare existing rows/sequences before committing. This was completed
locally; GCash changes remain stashed and no production rollback occurred.
See [[Database and Operations]].

## Replacement-first policy (2026-11-03)

The user approved defaulting new returns to the same approved returned products
and quantities at original paid prices. New replacement offers must equal the
approved return credit and have no payment or price-difference refund. Supervisor
approval checks and reserves eligible stock; a shortage automatically converts
the request to a refund and notifies the reseller. Complete receipt and Inventory
verification remain required, including custody of expired returned products.
Paid rejected-order refunds remain unchanged. Historical return-policy cases keep
their recorded workflows.

The authorized data reset is local transactions only. Preserve accounts, product
definitions, settings, unrelated conversations, batch quantities and non-sellable
custody. Require a private restored backup and reset rehearsal; do not restore
sold packs or reuse identifiers. This is separate maintenance, never startup.

Local implementation verification is complete: 1,027 regression cases and eight
post-maintenance synthetic checks passed. After verified private backup restores
and rehearsals, migrations through 049 and the authorized transaction-only reset
completed locally with stock/custody/protected data preserved. No production
deployment or deployed reset occurred. Sources:
`app/repositories/return_policy.py`, `app/repositories/product_returns.py`,
`app/repositories/replacement_workflow.py`, `tools/transaction_reset.py`.
Related: [[Replacement Workflow]], [[Returns and Financials]],
[[Orders and Inventory]], [[Database and Operations]], [[Testing and Change Impact]].

## Replacement stock shortage and no-money handling (2026-11-03)

The user changed new replacement handling: check and reserve eligible stock at
Supervisor approval; if insufficient, notify the reseller and convert to the
existing return-credit refund process. Complete company receipt and Inventory
verification remain required, including custody of returned products that have
expired. Sufficient-stock replacements must equal the approved credit and involve
no top-up or price-difference refund. Preserve the separate paid rejected-order
refund path unchanged. Sources: `app/repositories/replacement_workflow.py`,
`app/repositories/return_policy.py`, `app/repositories/product_returns.py`.
See [[Replacement Workflow]], [[Returns and Financials]], and [[Orders and Inventory]].

## Fixed replacement selection and connected review workspaces (2026-11-03)

After submission, Supervisor/Owner approval records the reseller's selected
replacement without offering an alternative or collecting approval explanation or
internal-note fields. Keep public explanations for rejection and mismatch
resolution. Compare returned items and the proposed replacement in Supervisor
Overview and retain the original-order link. Remove the Supervisor Inventory &
Replacement tab while retaining Inventory role actions and case history. A shortage
refund is surfaced in Settlement and cannot proceed before complete company receipt
and Inventory verification. Sources: `app/templates/portals/returns/_case_review.html`,
`app/templates/portals/returns/_supervisor_case_page.html`,
`app/static/js/return_details.js`, `app/repositories/replacement_workflow.py`.
See [[Replacement Workflow]] and [[Returns and Financials]].

### Legacy deferred-trigger compatibility

Limit the replacement-first constraint trigger to `NEW.return_policy_version=2`.
Queuing a no-op deferred check for historical rows unnecessarily prevented their
reconciliation and migration replay from using DDL in the same transaction.
Migration 049 applies this correction without editing already applied 048 or
rewriting historical business state. Source: `database/migrations/049_scope_replacement_policy_trigger.sql`,
`tests/test_return_workflow_handoff.py`, `tests/test_replacement_first_migration_reset.py`.

## 2026-11-02 — Define role-dashboard counts from destination eligibility

Use shared read-only scopes/predicates for counts and linked lists. Do not add
overlapping queues as a job total. Inventory expiry reads live warehouse batches
before worker pull-out records; payment/clearance waiting is separate from stock
actions. Stock rings count mutually exclusive active finished products, not packs
mixed with product counts. Low-stock KPI retains the destination's inclusion of
zero. Supervisor open cases count distinct authorized returns, including open
follow-ups after completion; pending refunds remain a separate entity/queue.
Unresolved inquiries include assigned and contacted prospects, not only pending.

Bound message previews through existing inbox helpers without advancing read
cursors. Preserve historic-assignment visibility rather than inventing a narrower
dashboard-only permission model. Purchase history uses recorded fulfilled line
prices separately from current catalog prices. These are projection definitions;
no payment, stock reservation or transition rule changed. See [[Role Dashboards]].

## 2026-11-02 — Follow the full Messenger refinement brief

The later pasted request explicitly groups Regarding and its order card above
the composer and replaces plain order links with message badges. This supersedes
the earlier top-selection and link-removal preferences. Keep case routing separate
from optional order association, preserve each message record, and place existing
hide/delete actions behind a native overflow menu with their existing confirmation.
See [[Background Work and Integrations]].

## 2026-11-02 — Show order context without splitting or filtering chat

Replace order filters with a scoped order inquiry card above the composer. Keep
Regarding at the top of the chat, default it to no order, and clear it after a
successful send. Historical card viewing alone does not select an order for the
next message. All lists person conversations; System alone lists automated order
notifications. This refines the earlier order-filter decision without changing
message IDs, pair identity, or financial workflows. See
[[Background Work and Integrations]].

## 2026-11-02 — Aggregate eligible histories by contact, retain case records

Group the reseller's replacement histories by current processor account and merge
the direct view only when that same account is the authorized direct participant.
Use a read projection rather than rewriting append-only case history. Replies and
read cursors keep their original case IDs, preserving handover and read-only rules.
Add nullable per-message order context to the existing unique direct pair instead
of creating order conversations. See [[Background Work and Integrations]].

## 2026-11-02 — Keep reseller conversations in one Inbox

Use one filterable conversation list and message stage for direct messages,
replacement cases, and order/refund updates. This removes the large replacement
index and banner above messaging while retaining separate conversation records,
assignment checks, and read cursors. Reuse the authorized return read for the
collapsible financial summary; keep payment and evidence actions in return details.
See [[Background Work and Integrations]] and [[Replacement Workflow]].

## 2026-10-29: Hide opening balances from Inventory Logs

The user requested hiding all opening data in Inventory Logs. Exclude opening
rows from the log projection after calculating historical stock totals, and remove
the Opening category from the page. Retain the ledger rows so subsequent movements
and expiry events still report correct balances. See [[Orders and Inventory]],
`app/repositories/inventory/queries.py`, `app/portals/team_leader.py`, and
`tests/test_inventory_log_balances.py`.

## 2026-10-29: Explicit reset with staggered opening stock

The user requested clearing database data while retaining accounts and finished
products, then setting 50 starting packs per product, split into 10-pack batches
with distinct expiries. Preserve linked reseller profiles and activation tokens
to keep account access intact, as well as schema migration history. Clear the old
operational records and stock ledgers in a single maintenance transaction, then
create five opening batches and matching movements per product. The user delegated
expiry selection; use 30, 45, 60, 75, and 90 days from the reset's Manila date.
These dates are reset inputs, not verified shelf-life rules. The reusable tool
requires an explicit first expiry and defaults to rollback. See
[[Database and Operations]], [[Orders and Inventory]], and
`tools/reset_operational_data.py`.

These are existing constraints observed in source/docs, plus the knowledge-vault
setup decision. This log does not invent the original authors' intent.

| Decision | Reason or observed constraint | Connection and source |
| --- | --- | --- |
| Keep a single FastAPI/Jinja2 app | Current implementation shares public and portal infrastructure | [[Architecture]]; `app/application.py`, `README.md` |
| Preserve inventory movement history | Auditability and traceable batch deductions are enforced | [[Orders and Inventory]]; `database/README.md` |
| Show historical whole-product balances in Inventory Logs (2026-10-29) | User found batch balances unclear beside product stock. Aggregate stock deltas before filtering and pagination, exclude non-sellable return receipts, and preserve stored batch snapshots for Product Details. | [[Orders and Inventory]]; `app/repositories/inventory/queries.py`, `app/templates/portals/team-leader/logs.html`, `tests/test_inventory_log_balances.py` |
| Keep returned goods non-sellable | Returned custody must not inflate available inventory | [[Returns and Financials]]; `docs/refund-return-process.md` |
| Separate customer follow-ups from settled return work (2026-10-29) | User approved a Returns-only Sales queue, existing Sales chat, optional receipt acknowledgment, tracked replacement issues, and required public rejection/mismatch explanations. Follow-ups never reopen Inventory or settlement. | [[Sales and Reseller Returns]]; migration 038, `app/return_experience.py`, `app/repositories/return_customer.py` |
| Assign pull-out destination automatically (2026-10-29) | User removed the holding-location form field. New expired-stock portal pull-outs use Expired holding; historical locations remain intact. | [[Orders and Inventory]]; `app/portals/product_returns.py`, `tests/test_expired_stock_location.py` |
| Show the first Pending pull-out transition in Inventory Logs (2026-10-29) | User requested visibility when stock expires. Read the existing unique queue record as a zero-delta status event, retain its original timestamp and quantities, and deduct stock only at physical pull-out. | [[Orders and Inventory]], [[Background Work and Integrations]]; `app/repositories/inventory/queries.py`, `tests/test_pending_pullout_logs.py` |
| Preserve Sales approval as the settlement authority | Migration 036 records authorization. The 2026-10-29 receipt decision below clears a complete match under the existing Sales approval; legacy reconciliation can retain the explicit confirmation action. | [[Returns and Financials]]; migration 036 |
| Preserve submitted report snapshots | Later refunds adjust current retained balances without rewriting original submissions | [[Returns and Financials]]; migration 034 and `docs/sales-report-refunds.md` |
| Check migration checksums | Applied schema history must remain identifiable | [[Database and Operations]]; `tools/migrate_database.py` |
| Report personal activity by original order dates (2026-10-29) | User selected personal recorded actions rather than assignment, and original order dates rather than action dates; return review does not claim a sale. | [[Sales Activity Reports]]; `app/repositories/activity_reports.py` |
| Freeze combined activity reports at submission (2026-10-29) | User requires the Owner, CSV, Excel and print to see the same document; later outcomes require regeneration. Legacy sales reports retain their existing adjustment rules. | [[Sales Activity Reports]], [[Returns and Financials]]; migration 037, `app/report_documents.py` |
| Store project knowledge as linked local Markdown (2026-10-28) | User requested Obsidian context; agents can read the same files without a plugin or running GUI | [[Start Here]]; `AGENTS.md`, `.obsidian/` |
| Consolidate Sales return review into Refunds (2026-10-29) | Earlier combined-workspace decision, superseded by the accepted-case processor handoff below. User requested one workflow for money refunds and product returns; reuse existing review, custody, authorization, and transfer records rather than adding another ledger. Dashboard totals reuse the report settlement calculation. | [[Returns and Financials]], [[Roles and Access]]; `app/portals/refunds.py`, `app/portals/product_returns.py`, `app/portals/team_leader.py` |
| Use a complete-match or mismatch Inventory receipt decision (2026-10-29) | User rejected partial-receipt entry and a second verification step. A complete proof/quantity/batch match records fixed Discarded inventory custody, verifies, and clears the existing Sales-approved resolution atomically. A mismatch notifies Sales and blocks release. Reseller acceptance, extra-payment checks, and authorized transfer processing remain enforced. | [[Returns and Financials]], [[Roles and Access]]; `app/repositories/product_returns.py`, `app/portals/product_returns.py`, `app/templates/portals/lifecycle/_inventory_return_dialog.html` |
| Reserve only after sufficient payment (2026-10-29) | User removed unpaid holds. Payment-time locks recheck stock; shortages reject into payment verification/refund review. Fulfillment clears the hold with one physical deduction, preserving availability and traceability. | [[Orders and Inventory]]; migration 040, `app/repositories/orders.py` |
| Transfer accepted return processing to Supervisor/Owner (2026-10-29) | User limits regular leaders to the initial decision and assigned tracking. Apply to accepted unfinished cases, including open post-release follow-ups, while retaining custody, approval, and money history. | [[Returns and Financials]], [[Roles and Access]]; migration 041 |
| Remove Owner action OTPs (2026-10-29) | User requires signed-in Owner actions without extra emailed codes; capabilities, CSRF, audits, activation, and password verification remain. | [[Roles and Access]]; `app/portals/owner.py` |
| Keep internal conversation media private and read cursors bounded (2026-10-29) | User requests images/videos and unread badges for internal conversations only. Participant authorization and displayed-message cursors prevent exposing files or clearing unseen replies. | [[Background Work and Integrations]]; migration 042 |
| Record order receipt separately from fulfillment (2026-10-30) | User requires an Order received button without disabling eligible returns. Keep the original fulfillment clock and stock ledger; record a once-only reseller acknowledgement separately, while reseller history displays Completed. | [[Orders and Inventory]]; migration 043, `app/repositories/orders.py`, `tests/test_conversation_order_receipts.py` |
| Consolidate Owner navigation around business decisions (2026-10-30) | User requested a portal overhaul and explicitly allowed removing navigation. Reduce 19 sidebar destinations to nine while retaining authorized detailed URLs as contextual tabs; keep staff responsibility and explicit Owner intervention. | [[Owner Portal]], [[Roles and Access]]; `app/portal_config.py`, `app/templates/portals/owner/` |
| Keep Owner financial measures distinct (2026-10-30) | Reuse reconciliation totals for retained sales, obligations, and collections; identify the chart as gross fulfilled reseller sales and report documents as snapshots. Do not invent profit or bank balances. | [[Owner Portal]], [[Returns and Financials]], [[Sales Activity Reports]]; `app/repositories/owner_insights.py`, `app/templates/portals/owner/dashboard.html` |
| Include return processing in staff handover (2026-10-30) | Prevent stranded accepted cases/open follow-ups and assignments to staff without processing permissions. In-progress financial attempts require explicit outcome/takeover and preserve their actual processor. | [[Owner Portal]], [[Roles and Access]]; `app/repositories/superuser.py`, `app/repositories/accounts.py`, `tests/test_owner_overhaul.py` |
| Version replacement requests and move new submissions directly to Supervisor (2026-10-30) | User selected reseller product choice/comparison, an immediate private conversation, receipt before top-up, verified funds before FEFO preparation, Inventory release, and Supervisor delivery. Same-product matched packs keep original prices; extras use catalog prices, cheaper selections refund the difference, paid selections stay fixed, and existing cases keep earlier rules. | [[Replacement Workflow]], [[Returns and Financials]]; migration 044, `app/repositories/replacement_workflow.py` |

| Keep local Wi-Fi sharing opt-in (2026-11-01) | User requested access for a friend on the same Wi-Fi. A local environment override changes only the app binding; default loopback access and private database/mail ports remain. | [[Database and Operations]]; `compose.yml` |

Add new entries only when a task establishes a consequential decision. Include
the date, rationale, source/evidence, and affected notes. Do not infer deployment
status or business approval from the existence of a note.

## 2026-11-03 — Require Owner-issued reseller registration invitations

Keep external discussion and approval outside MEATTRACK. The public chatbot only
shows the Business Owner's verified email. An active Owner issues a unique,
email-bound, seven-day invitation; the invitee creates a pending account and must
verify the bound email through the existing OTP mechanism before reseller access
is activated. Store only the invitation-token hash, consume the invitation
atomically with activation, and keep other staff account activation unchanged.
Enable login email OTP by default in all environments. This replaces visitor
lead capture and inquiry approval as a path to creating new reseller accounts.
See [[Roles and Access]], [[Owner Portal]], [[Background Work and Integrations]],
and [[Database and Operations]]. Sources: migration
`database/migrations/050_reseller_registration_invitations.sql`,
`app/repositories/reseller_invitations.py`, `app/repositories/accounts.py`,
`app/routers/reseller_registration.py`, and `app/chatbot/lead_flow.py`.

- 2026-11-01: Replace the initial view-only low-stock threshold with one saved
  business setting (default 10 packs), following the user's confirmed choice.
  Owners review alerts independently; Inventory team retains stock responsibility,
  with no individual assignment. Stock conditions control automatic clearance;
  manual closure cannot hide an active stock risk. Keep notification reads separate.
  Migration 045 and `app/repositories/stock_alerts.py` implement these boundaries.
  See [[Owner Portal]] and [[Background Work and Integrations]].

- 2026-11-01: Connect Inventory and Owner alerts through one workspace data loader
  and shared presentation. Reuse stock episodes and saved policy; do not copy
  records or give Inventory Owner review/closure powers. Inventory dashboard
  low-stock counts use live availability so stale alert records cannot change the
  displayed product count. See [[Owner Portal]] and [[Orders and Inventory]].

## Inventory alert card destinations — 2026-11-01

Inventory's three stock-risk summary cards open category detail pages with
matching live records and inclusion rules. Use category query URLs on the existing
Alerts route to retain its authorization and navigation context, with an explicit
Back link. Share row rendering with the summary so stock quantities/actions stay
consistent. This implements the requested card drill-down without changing stock
eligibility or alert closure rules. See [[Orders and Inventory]]. Sources:
`app/portals/team_leader.py`, `app/templates/portals/_stock_alerts.html`, and
`app/templates/portals/team-leader/alert-detail.html`.

## Make expired alerts actionable before the queue scan - 2026-11-01

Read pending pull-outs from live expired batch balances and optionally join their
worker queue records. Stock Alerts already detects live expiry, so requiring a
queue row could send staff to a page without the alerted batch while the worker
was delayed. Keep GETs read-only, preserve worker-created historical queue events,
and retain the transactional pull-out/disposal operations. Batch-specific links
and live progress connect the two pages; completing a warehouse pull-out does not
require disposing of the held goods first. See [[Orders and Inventory]] and
[[Testing and Change Impact]]. Sources: `app/repositories/product_returns.py`,
`app/portals/product_returns.py`, and `tests/test_alert_pullout_connection.py`.

Owner alert card navigation - 2026-11-01: use the same category detail template
as Inventory with explicit portal-role parameters. Owner cards now open matching
lists instead of page anchors; links stay in the Owner portal. Existing review
and stock rules remain unchanged. See [[Owner Portal]]. Sources:
`app/portals/owner.py`, `app/templates/portals/_stock_alert_detail.html`.


## 2026-11-01 - Integrate standalone stock tools

Add raw-material adjustments, batch archives, package contents/dispatch, and
product photos without importing the standalone database or its expiry policy.
Use shared product locks and FEFO allocation to preserve order/replacement holds.
Keep the start-of-expiry-date block and route expired goods through Pull-outs,
preserving custody/disposal semantics. Require legacy package balances and holds
to clear before component tools are used, retaining existing history. Manual
package dispatch creates no sale; standard contents lock once, and customization
is per dispatch. Store public product photos in PostgreSQL to survive redeploys.
Sources: migration 046, `app/repositories/inventory/tools.py`,
`app/portals/inventory_tools.py`, and `app/uploads.py`. See [[Orders and Inventory]].

## 2026-11-02 — Reseller owns submitted payment details

Use the same receipt-review component for ordering, history, and version-2
replacement payments. Supervisor/Owner verifies actual funds against the stored
reseller submission rather than entering another reference or amount. This keeps
collection verification tied to the exact evidence and current quote. Legacy
version-1 reconciliation remains available. See [[Replacement Workflow]] and
[[Orders and Inventory]].

## 2026-11-02 — Include historical cases in portal receipt review

Live Supervisor case review exposed a separate version-1 dialog and image-only
submission path. Apply signed reseller receipt review and read-only staff payment
values to all portal cases. Missing historical fields require reseller resubmission;
never infer reference/amount from the amount due or ask staff to enter reseller
values. Preserve old stock/custody rules and evidence. See [[Replacement Workflow]].

## 2026-11-02 — Render login CSRF tokens before client scripts

A reported Supervisor sign-in failure exposed login forms depending on JavaScript
token injection. Render tokens server-side, prevent login-page caching, and return
a retry form for rejected browser login tokens. Keep token validation, HTTP 403,
and session rotation intact. See [[Roles and Access]].

## 2026-11-02 — Queue only actionable payment reviews

Require the reseller's current confirmed details before assigning staff payment
verification. Version-2 payment review must not depend on later stock reservation.
This aligns queue counts and next actions with the signed receipt workflow while
preserving historical version-1 reservation rules. See [[Replacement Workflow]].

## 2026-11-02 — Bind confirmation to the payment staff reviewed

Read-only payment fields must not silently confirm a newer reseller submission.
Compare a reviewed-state fingerprint under the case lock before recording funds.
Changed metadata, quote, or evidence requires a reload. See [[Replacement Workflow]].

## 2026-11-02 — Give Supervisor return review a dedicated page

Use the existing case query route as a full details page rather than a long modal.
Separate overview, review, stock, settlement, and audit history with tabs, and keep
available decisions in a persistent action bar. This makes evidence and money
comparisons easier to review while preserving existing handlers, permissions,
required reasons, signed review tokens, and audit records. A five-stage summary
may show interleaved work; it must not advance the underlying workflow.
See [[Sales and Reseller Returns]] and [[Replacement Workflow]].


## 2026-11-02 — Share the dedicated return layout with resellers

Keep the history ledger and new-request dialog, but use a dedicated page when a
reseller tracks a case. Group public facts, replacement/delivery, payment/refunds,
evidence, and activity into tabs, with the same milestone summary and persistent
actions as Supervisor review. Reuse the signed order receipt form and existing
role projections rather than adding a separate upload or staff-style editing path.
Show replacement pricing once within its tab; use confirmations for the existing
revision acceptance and optional acknowledgment actions. See
[[Sales and Reseller Returns]] and [[Replacement Workflow]].

## 2026-11-03 — Retire portal inquiry review

Remove the Owner and Supervisor reseller inquiry pages and their decision
endpoints because visitor interest now goes directly to the Business Owner
outside MEATTRACK. Keep historical rows for audit continuity; do not erase
their database records. The Owner invitation and email OTP workflow remains
the only account creation path for new resellers. See [[Owner Portal]] and
[[Roles and Access]]. Sources: `app/portal_config.py`,
`app/portals/owner.py`, `app/portals/team_leader.py`.

## 2026-11-03 — Retire visitor-to-Sales Live Chat

Visitors no longer need a Sales conversation inside MEATTRACK. Keep the public
chatbot for product answers and Owner contact details, and keep authenticated
reseller messaging for orders and returns. Remove the chatbot handoff, Sales
Live Chat workspace, and visitor conversation APIs while retaining historical
chat data for the existing retention job. See [[Background Work and Integrations]]
and [[Roles and Access]]. Sources: `app/chatbot/lead_flow.py`,
`app/routers/chatbot.py`, `app/routers/live_chat.py`, `app/portal_config.py`.
