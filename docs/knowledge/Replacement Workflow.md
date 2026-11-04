---
tags: [meattrack, replacement, returns, payments, inventory]
verified: 2026-11-04
---
# Replacement workflow

## Replacement-first policy update (2026-11-03, verified locally)

New submissions retain workflow version 2 and carry separate `return_policy_version=2`.
Their initial replacement is derived from the approved returned allocation
products/quantities at original paid prices, including when catalog prices change.
New direct refund requests and reseller-selected substitutes are rejected. Once
submitted, the replacement selection is fixed during Supervisor/Owner approval;
approval records the submitted selection without alternative-product or approval
notes fields. No top-up or price-difference refund is part of a policy-2 replacement.

If stock is insufficient at Supervisor approval, the case automatically converts
to the existing refund process and notifies the reseller. Other authorized
pre-release conversions preserve case history and return the approved credit.
Complete Inventory receipt still gates settlement; converted cases do not require
replacement delivery.

The following original selection/difference-refund descriptions apply to historical
policy-1 cases. Those cases retain their recorded behavior. The current Reseller
request form derives the replacement and supports input-preserving comparison and
submission retry. Supervisor pages and Owner dialogs share confirmation, inline
validation and error recovery. Converted offers remain historical without
acceptance/payment/release actions; the grouped Inbox shows refund components. The
final 1,027-case regression, verified local restore/reset and eight post-maintenance
smoke checks passed; all five role queues were checked read-only. This does not
establish production deployment. Current flow diagrams are in
`docs/diagrams/05-replacement-first-policy.md`.
Sources: `app/repositories/return_policy.py`, `app/repositories/replacement_workflow.py`,
`app/repositories/product_returns.py`, migration 048,
`app/reseller_contact_history.py`, `app/static/js/reseller_contact_history.js`,
`app/static/js/forms.js`, `app/static/js/return_details.js`,
`tests/test_replacement_first_policy.py`, `tests/test_replacement_first_stock.py`,
`tests/test_replacement_first_portal.py`, `tests/test_replacement_first_converted_history.py`.
Migration 049 scopes the deferred policy check to new-policy rows; legacy rows
retain their existing reconciliation/migration behavior. See [[Database and Operations]].
See [[Decisions]], [[Returns and Financials]], [[Testing and Change Impact]].

## Approval stock decision and no-money replacements (2026-11-03)

Supervisor approval now locks eligible FEFO stock and reserves the approved
quantities. If any requested product is short, the case is converted to the
existing refund process and the reseller is notified in the same transaction.
Refund settlement still waits for complete return receipt and Inventory
verification. Returned products remain non-sellable; expired products are
recorded in **Expired holding** even when the stock shortage caused the refund.

Version-2 replacements must equal the approved return credit. No top-up or
price-difference refund is collected for a replacement. Paid-order rejection
refunds retain their separate existing workflow. Sources:
`app/repositories/replacement_workflow.py`, `app/repositories/return_policy.py`,
`app/repositories/product_returns.py`, `docs/diagrams/05-replacement-first-policy.md`.
See [[Returns and Financials]], [[Orders and Inventory]], and [[Decisions]].

## Fixed selection and aligned Supervisor review (2026-11-03)

Supervisor and Owner review the reseller's submitted replacement as-is. The
Supervisor Overview compares requested return items and the proposed replacement
side by side, with a responsive stacked layout on narrow screens and a full link to
the original order. Supervisor actions for receipt history, replacement preparation,
release, and delivery remain in their role workspaces; the dedicated Supervisor
Inventory & Replacement tab is removed. A stock-shortage conversion opens Settlement
and displays the refund amount, refund workspace link when available, and the
mandatory company receipt/Inventory verification gate. Sources:
`app/templates/portals/returns/_supervisor_case_page.html`,
`app/templates/portals/returns/_case_review.html`,
`app/static/js/return_details.js`, `app/static/css/return_details.css`,
`app/repositories/replacement_workflow.py`.

The later 2026-11-02 Inbox refinement groups eligible reseller histories by current
processor account, including existing direct messages when that account is also
the current direct-chat leader. It retains each case's underlying messages,
permissions, identifiers, read cursor, and declined-case write restriction.
The reseller chooses a case in the shared view when replying about a replacement.
Regular Sales does not gain access to a different Supervisor's private case chat.
This supersedes the earlier reseller case-row navigation description; staff views
retain their existing layout. See [[Background Work and Integrations]]. Sources:
`app/reseller_contact_history.py`, `app/static/js/reseller_contact_history.js`,
`app/repositories/replacement_messages.py`.

New reseller replacement submissions use **replacement workflow version 2**.
Existing cases and refund-only requests keep version 1 and their original rules,
quotes, evidence, approvals, stock history, and completion behavior. This is the
user-directed replacement process introduced on 2026-10-30, not a reinterpretation
of historical cases. See [[Returns and Financials]], [[Orders and Inventory]],
[[Roles and Access]], and [[Sales and Reseller Returns]].

```mermaid
flowchart TD
    A[Select original returned packs and attach proof] --> B[Choose replacement products]
    B --> C[Review server-calculated comparison]
    C --> D[Submit directly to Supervisor or Owner fallback]
    D --> E[Linked private conversation available]
    E --> F{Supervisor approves?}
    F -->|No| G[Decline with public explanation]
    F -->|Yes| H{Enough eligible stock?}
    H -->|No| RF[Notify reseller and convert to refund]
    RF --> I[Receive all approved returned goods, including expired goods]
    I --> V[Inventory verifies receipt; refund follows existing settlement]
    H -->|Yes| RS[Reserve eligible FEFO stock]
    RS --> J[Confirm complete return receipt into non-sellable custody]
    J --> K[Inventory prepares and marks replacement ready]
    K --> L[Inventory confirms warehouse release]
    L --> M[Supervisor confirms delivery]
    M --> N[Complete replacement with no money involved]
```

## Request and recorded comparison

The reseller starts from a fulfilled order within its unchanged 120-hour eligibility
window, selects traceable original allocation quantities, gives a reason, and
attaches 1–3 supporting photos. They then select active finished replacement
products in whole packs and review the comparison before submitting.

The comparison builder is server-side. It calculates credit from **selected returned
packs at original purchase prices**, never from the whole order. Matching quantities
of the same product retain their original paid prices; additional packs and
different products use catalog prices at comparison time. Multiple original price
lines match in ascending original allocation ID order. Line totals are authoritative;
separate pricing components retain each original/catalog contribution, even when
an average display unit price has to be rounded.

An account/order-bound signed comparison expires after one hour. Submission
rechecks eligibility, remaining unclaimed quantities, products, and comparison
values. A stale/tampered comparison requires review again. The submitted comparison
becomes immutable offer revision 1 and counts as the reseller's acceptance of that
selection. Same-product/no-difference requests still retain their comparison.
The original order and ordinary sales ledger are not recreated.

Submission, quote publication, direct Supervisor assignment, initial conversation,
evidence, and notifications commit in the same transaction. An available Supervisor
is selected through the existing assignment helper, preferring an eligible original
Supervisor. Without one, Owner fallback applies. Regular Sales leaders track their
assigned reseller's progress but cannot approve or process a version-2 request.

The assigned Supervisor can approve unchanged products without repricing them.
Pre-payment changes to replacement products publish another comparison revision
and require reseller acceptance. An unresolved receipt mismatch can revise approved return quantities within original
delivery/remaining-claim bounds; it recalculates credit and publishes a new comparison
requiring fresh acceptance. Inventory still confirms the entire revised approved
request, never a partial receipt. Paid or released selections cannot be revised.

Sources: `app/repositories/replacement_workflow.py`,
`app/repositories/product_returns.py`, `app/portals/product_returns.py`,
`app/templates/portals/reseller/_return_request_dialog.html`.

## Receipt, preparation, and delivery

Inventory is notified after approval. The complete-match/mismatch receipt decision
is retained, including approval revision checking. Complete receipt records goods
in the existing custody/holding ledger with source **return**, linked to original
allocations and the case. The **Pull-outs** workspace includes separate returned-
goods and expired-stock records. Returns never add sellable stock or deduct the
original sale a second time. Physical disposal remains a separate recorded action.
Expired returned batches are recorded at **Expired holding**; the receipt header
retains its request location while each line keeps its own traced custody location.

Stock is checked and reserved during Supervisor approval using existing FEFO
locking and allocation. If any requested quantity is unavailable, the request is
converted to a refund, but complete return custody and verification remain required.
For sufficient stock, Inventory's **Prepare and mark ready** action requires
complete receipt and current acceptance/authorization. Preparation records
the actor/time and notifies Supervisor/Owner; it does not deduct physical stock.
Warehouse release remains a separate Inventory action, revalidating stock, expiry,
reservations and quote, then recording exactly one physical deduction.
Expiry blocks a batch starting midnight on its printed date in Manila.

If stock expires after approval, preparation/release remains blocked until eligible
stock is available again. The existing expiry worker clears invalid replacement
holds, while request-time checks remain authoritative if the worker is unavailable.

Release makes delivery outstanding. The assigned Supervisor/Owner records delivery
separately. Version-2 completion waits for delivery. Acknowledgement and
post-release follow-ups retain their separate meanings.

Sources: `app/repositories/product_returns.py`, `app/repositories/refund_integrity.py`,
`app/repositories/inventory/allocation.py`, `app/receipt_ocr.py`,
`app/templates/portals/reseller/_replacement_payment.html`,
`app/templates/portals/lifecycle/_inventory_return_dialog.html`.

## Conversation, access, and connected reporting

Each version-2 case has a private persistent text conversation. It appears in the
existing reseller Inbox and Supervisor Reseller Messages workspaces. Owner fallback
uses a secondary replacement-conversation view, not a restored general chat-admin
page. The comparison links directly to this conversation; payment stays in Returns.
Proof and supporting photos remain in the authorized linked return details.

The case messenger reuses the current Sales/reseller conversation layout and
message/composer classes. Desktop shows a case list beside the active thread;
phones open the thread first, with a keyboard-accessible back button to choose a
case. Participant photos honor existing account crop settings, with a user-icon
fallback. Rows show the last message and unread count, ordered by latest message
activity. The message/read endpoints return the current case unread count so
reading displayed messages clears the badge and incoming messages remain unread
while the mobile list hides the thread. A compact strip identifies the original order and case status and links
to **Open return details**. The reseller Inbox integrates these rows with direct
and order/refund conversations in one searchable list. Selecting a case retains
the unified workspace and shows a collapsible comparison from the authorized
return read. Staff views retain their existing case list. The Sales Supervisor
Messages page places direct reseller and replacement threads in one workspace
with separate category tabs and the same conversation-list/reading-pane layout;
the replacement pane prompts for case selection. A replacement deep link opens
its category. Team
Leader direct messaging remains unchanged because that role does not have private
replacement case access. This does not merge message history or permissions. Checked 2026-11-04
against `app/templates/portals/team-leader/reseller_messages.html` and
`app/portals/shared.py`. Checked 2026-11-02 against
`app/templates/portals/reseller/inbox.html`,
`app/templates/portals/reseller/_inbox_replacement.html`, and
`app/portals/reseller.py`; see [[Background Work and Integrations]].

Own replies use the standard outgoing bubble, counterpart replies the incoming
bubble, and automatic case messages the existing system style. Enter sends,
Shift+Enter inserts a line, and IME composition is preserved. Failed sends retain
the draft and UUID for safe retry; loading errors provide a retry control. Hidden
mobile threads do not advance their read cursor. This is a presentation change:
payment stays in Returns, supporting evidence stays in return details, and existing
assignment permissions and declined-case read-only rules still apply. Presentation
and role views checked 2026-10-31; see [[Testing and Change Impact]].

Both users need not be online, and no first reply is required before approval.
Messages poll every three seconds while visible. Unread badges count conversations;
read cursors advance only to real messages displayed in the visible thread.
Client UUID retries cannot insert duplicate messages or reuse an ID for different
content. Message history rejects update/delete/truncate. Declined cases are read-only.

Authorization derives from the case's reseller and current Supervisor assignment;
Inventory, unrelated users, and the original regular Sales leader cannot read/send
private conversation messages. Owner access follows existing broad capabilities.
Staff handover changes access without copying or deleting conversation history.
See [[Owner Portal]] and [[Background Work and Integrations]]. Supervisor delivery
events qualify as personal return activity in [[Sales Activity Reports]] without
claiming another sale.

Sources: `app/repositories/replacement_messages.py`, `app/conversation_notifications.py`,
`app/portals/shared.py`, `app/templates/portals/_replacement_conversation.html`,
`app/static/js/replacement_workflow.js`, `app/static/css/replacement_workflow.css`,
`app/templates/portals/_replacement_thread_rows.html`,
`app/templates/portals/_replacement_conversation_links.html`,
`app/repositories/activity_reports.py`.

## Database and rollout

Migration `044_replacement_workflow.sql` adds version/preparation/delivery/payment
metadata and quote pricing components, plus append-only case messages and bounded
read cursors. Existing rows default to version 1; only the new replacement form's
submission path opts into version 2. The baseline includes the same additions.
Applied earlier migrations are unchanged. The explicit reset tool's reviewed table
list includes the new conversation tables; that destructive tool was not run.

Apply 044 before running compatible app/worker code. On 2026-10-30, the local
development target was verified as loopback PostgreSQL on port 55433, app/worker
writes were paused, 044 was applied through the migration runner, and both local
services resumed. This records a local upgrade only, not a Contabo deployment.
See [[Database and Operations]] and [[Testing and Change Impact]].

## Payment review presentation (2026-11-02)

Replacement checkout reuses the ordering receipt dialog with the replacement-scoped
scan endpoint and token. Upload alone never enables submission: the reseller must
review and confirm complete payment details. Supervisor/Owner version-2 review
displays the submitted reference, amount, and Manila date/time as read-only text.
Verification reads those values from the current stored submission, regardless
of posted reference/amount, and still requires explicit provider funds confirmation.
Historical image-only portal submissions require reseller resubmission through receipt review before staff verification. See [[Orders and Inventory]]
and [[Returns and Financials]]. Sources: `app/repositories/product_returns.py`,
`app/templates/portals/returns/_payment_review.html`,
`app/templates/portals/reseller/_replacement_payment.html`, `app/static/js/portal.js`.

## Older-case portal payment review (2026-11-02)

The same receipt review now applies to version-1 portal payments without changing
those cases' stock-reservation prerequisite. The reseller receipt-scan endpoint
returns the shared widget's success flag and a replacement-case/file-bound token.
All portal payment uploads validate the token and confirmed fields, then store
payment metadata against the current quote revision. Supervisor/Owner portal
verification requires those stored details and explicit funds confirmation; posted
staff reference/amount cannot replace them. Historical image-only proof remains
visible but cannot be verified through the portal until the reseller resubmits.
Lower-level legacy reconciliation primitives remain for existing integration paths.

The older Supervisor/Owner dialog now puts payment review first and groups request/
quote, Inventory/approval records, attachments, and activity into collapsed cards.
See [[Returns and Financials]] and [[Testing and Change Impact]]. Sources:
`app/portals/product_returns.py`, `app/repositories/product_returns.py`,
`app/templates/portals/team-leader/returns.html`,
`app/templates/portals/returns/_payment_review.html`,
`app/templates/portals/reseller/_return_tracking_dialog.html`.

## Payment queue readiness (2026-11-02)

Payment verification queues require current-revision reseller reference/amount
metadata and current proof. An image-only historical submission waits on the
reseller instead of advertising a staff verification action. Version-2 submissions
can enter payment review before stock reservation; version 1 retains its existing
reservation prerequisite. SQL queue counts, list filtering, public status, and
next-action resolution use the same readiness rules. Version-2 progress enrichment
preserves issue/reconciliation actions and does not request another payment while
submitted details await verification. Sources: `app/return_experience.py`,
`app/repositories/replacement_workflow.py`, `tests/test_return_experience.py`,
`tests/test_return_customer.py`, and `tests/test_replacement_workflow.py`.

Portal funds confirmation also binds to a fingerprint of the reviewed case,
quote revision, reseller payment metadata, and current-revision proof IDs. The
repository compares this fingerprint while holding the case lock. A resubmission
invalidates the open staff form and requires reloading before confirmation. Older
cases with invalid/expired reservations first wait on Inventory, preserving their
reservation prerequisite. Sources: `app/repositories/product_returns.py`,
`app/portals/product_returns.py`, `app/templates/portals/returns/_payment_review.html`,
`tests/test_replacement_workflow.py`.


## Role-dashboard queue projections (2026-11-02)

[[Role Dashboards]] shares read-only version-1/version-2 eligibility predicates
with filtered return destinations. Preparation/release remain separate from
payment or clearance waiting, and Supervisor delivery follows Inventory release.
Open follow-ups remain visible after completion. Transaction handlers still
authorize every action; dashboard reads do not advance state or message cursors.
Sources: `app/repositories/dashboard_queues.py`, `app/repositories/portal_dashboard.py`,
`app/portals/product_returns.py`.


## Five-outcome live review (2026-11-03)

The [[Return Workflow Gap Review]] predates the 2026-11-03 stock-shortage and
no-money policy change. Its control/UX findings concern separate portal operations
and remain historical review context.


## Owner stock oversight (2026-11-04)

The read-only Replacement tab under Owner Inventory lists replacement requests
and their status, including requests converted to refunds. Refund-only requests
remain in Returns & refunds. It reuses the existing case status projections and
links to authorized Owner case details; it does not change custody, approval,
release or refund rules. See [[Owner Portal]] and [[Roles and Access]]. Sources:
`app/portals/owner.py:replacements_context`,
`app/repositories/product_returns.py:list_product_returns`,
`app/templates/portals/owner/replacements.html`.
