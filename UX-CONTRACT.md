# MEATTRACK portal UX contract

Scope: five role dashboards and linked read-only queue filters. This does not
replace the transactional workspaces' specifications. English UI, peso formatting,
Gregorian dates and Asia/Manila business time. Accessibility target: WCAG 2.2 AA;
test evidence below does not imply a full conformance audit. Visual context:
`DESIGN.md`; existing runtime CSS owns tokens.

## Business context

Roles: `docs/knowledge/Roles and Access.md`, `app/capabilities.py`,
`app/portal_dependencies.py`. Returns/payment: `app/return_experience.py`,
`app/repositories/product_returns.py`, `app/repositories/replacement_workflow.py`.
Stock: `finished_stock_availability`, `all_stock_reservations`,
`app/repositories/inventory/`, `docs/knowledge/Orders and Inventory.md`.
Executable invariants and checked tests take precedence over stale notes.
Dashboards change read-only projections, not financial or inventory transitions.

## Canonical UI Map

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Date | `app/business_time.py`, `app/web.py:nice_date` | Business clock and formatter | Read-only text; no picker added | Portal context and browser tests |
| Scrollbar | Global `portal_base.css` tokens | Existing runtime CSS, mirrored by DESIGN.md | Natural document flow and bounded native dialogs | Responsive overflow and computed-style checks |
| CRUD | Existing guarded portal destinations; return action/confirmation adapters | Capabilities and transactional repository guards | Read-only dashboard links; guarded return mutations | Isolated workflow and browser regressions |
| Select/Listbox | Existing native selects in shared return review/order picker | Return templates and server product IDs | Platform-owned popup geometry is accepted | Keyboard/open-popup checks |
| Form | `app/static/js/forms.js`; request comparison adapter in `replacement_workflow.js` | Server validation and shared field errors | Comparison-first request and processor actions | Invalid fields, focus, retry and discard checks |
| Toast | Existing server-rendered notice/status regions | Confirmed server response | Persistent actionable errors and polite status messages | Failure and stale-state browser checks |

### Return-workflow extension (2026-11-03)

This extension covers return requests, detail pages for Reseller, Sales,
Supervisor/Owner and stock-only Inventory, and the Owner dialog. Business authority:
`docs/knowledge/Decisions.md` (replacement-first
policy), `app/repositories/return_policy.py`, and transactional repository guards.
Historical cases retain their recorded policy; a client cannot choose one.

| Capability | Canonical owner | Source of truth | Allowed variants | Verification |
|---|---|---|---|---|
| Select/Listbox | Existing native selects in shared return review/order picker | Return templates and server product IDs | Platform-owned popup geometry is accepted; no authored select introduced | Browser keyboard/open-popup checks |
| Date | Existing receipt widget, `app/business_time.py`, `nice_date` | Stored receipt metadata and Manila business time | Native date/time inputs in receipt review; read-only elsewhere | Receipt review and scoped-token tests |
| Form | `app/static/js/forms.js`; request comparison adapter in `replacement_workflow.js` | Server validation; shared field-error adapter | Standard detail actions and comparison-first request; no native validation bubbles | Invalid fields, labels, first-error focus and retry tests |
| Toast | Existing server-rendered notice/status regions | Confirmed server response | Persistent actionable errors and polite success/status messages | Browser failure and stale-state tests |
| CRUD | Existing return actions and shared `return_details.js` confirmation | Capabilities, assignment and case fingerprint | Return to current case/tab; preserve originating list filter | Full replacement/conversion journeys |
| Scrollbar | Global `portal_base.css` tokens | Existing runtime CSS, mirrored by DESIGN.md | Document scrolling and bounded native dialogs | Computed styles, narrow/short viewport and forced-colors checks |

Inventory return details retain their tabs and originating queue state after actions.
Show current responsibility once. History defaults to short event rows with native
note/record disclosures; full milestone and custody records remain available on demand.
The generic success toast is not repeated in an inline card. Scoped presentation
uses `inventory-returns.css`; filters, forms, messages and stock permissions retain
their canonical owners above.

Requests show the same returned products as the default replacement; Supervisor
offers alternatives. A comparison is a price review, never a stock reservation.
Quantity edits invalidate comparison tokens; old responses cannot restore them.
Failed comparisons preserve inputs and offer an explicit retry. Confirmed receipt
metadata and provider-funds verification remain separate steps.

Refund conversion uses the existing app-owned confirmation dialog and a shared
form partial in processor surfaces. Show the return credit, verified top-up and
refund total; require a customer explanation and internal supply reason. When
payment evidence is unresolved, require an explicit funds-not-received decision
or verification before conversion. Initially focus Cancel in the confirmation.
Never expose these staff actions or payment evidence to Inventory.

New-policy offer review uses the same confirmation and input-preserving server
error adapter in Supervisor pages and Owner dialogs. An invalid alternative keeps
the selected products and explanations for correction. Discard resets both values
and conditional requirements. Field errors do not change a control's accessible name.

Converted cases retain comparisons as offer history without acceptance, payment or
delivery instructions. The grouped Inbox uses the same public return projection
for credit, verified top-up and total refund. Receipt work remains available to
Inventory; monetary fields remain excluded from its projection.

Dashboard tables have no selection, sort controls or editable forms. Data is
bounded. Destination form/select/toast components remain owned by those workspaces;
no alternative is introduced. There is no new modal or async dashboard mutation.

## Navigation and state

Links open existing authorized workspaces. Normal guards run before derived queue
validation. Unsupported cross-role derived filters return HTTP 400. Every queue
remains visible, including zero; independent queues may overlap and must not be
summed as distinct jobs. Use existing status/queue filters wherever they match.

Derived `dashboard_queue`: Inventory `receipts`, `prepare`, `release`; Supervisor
`delivery`, `followups`; Reseller `customer_action`. Filters have visible labels
and clear actions, preserve pagination query state and do not change default views.
Counts span the full authorized matching dataset, independently of preview limits.
Supervisor `status=open` includes unresolved returns and completed cases with open
follow-ups. Inquiry `status=open` includes pending, assigned and contacted records.
Conversation links identify an existing direct reseller or replacement case.

## Read-only behavior and recovery

Dashboard GETs do not write workflow events, receipt custody, ledgers, reservations
or message-read cursors. Existing shell/session behavior is unchanged. Destinations
recheck permissions and current state before an action. Server-rendered data has
no new optimistic state, client cache or background write. Navigation/reload fetches
fresh data; existing app/auth error handling remains authoritative. Do not replace
failed reads with plausible zero counts. Empty data has concise explanations.
Keyboard behavior follows native anchors/disclosures with visible focus.

## Presentation contracts

Available, reserved and physical stock retain separate definitions. The stock
ring partitions active finished products into above-warning, positive-low and
zero-stock categories. The existing low-stock KPI includes zeros and says so.
Expiry uses saved warning periods; the KPI excludes expired batches, while risk
rows show expired warehouse batches first, including before worker-created
pull-out episodes. Risk quantities are physical packs before reservations.

Supervisor open cases count returns once, including open follow-ups after
completion. Pending refund records and failed refunds remain separate. Order
segments partition all assigned reseller orders, all time. Message previews are
bounded and retain existing participants, historic-assignment and deletion rules.
Reseller purchase history uses recorded fulfilled line prices separately from
current catalog prices. No direct reorder or early-reservation claim is introduced.
Sales Leader initial-review/tracking duties do not gain Supervisor refund controls.

## Verification

`tests/test_role_dashboards.py` checks counters against destination predicates,
permissions, read-only GETs, stock/payment boundaries, reassignment, pagination
and original purchase values in disposable PostgreSQL schemas.
`tests/test_role_dashboards_browser.py` renders real templates and checks links,
keyboard disclosures, zero queues, reduced motion, long content, images and
horizontal overflow at 320/390/768/1024/1440/1920px. It captures desktop/phone
screenshots for all roles. DPR-2 viewport tests cover zoom-equivalent reflow,
not physical-device or manual browser-zoom verification. Existing inventory,
return and payment regressions cover unchanged business transitions.

## Profile password verification

All five roles reuse `portals/_password_change.html`, `forms.js` field validation,
server notice regions and `app/portals/password_change.py` request/verify/save/
cancel actions. The stages are current password -> email OTP -> new password.
Only confirmed provider acceptance shows OTP entry; only a verified, unexpired
server grant shows password entry. Invalid input uses inline errors, first-error
focus and no native bubbles. Failed delivery returns to request; wrong codes
remain at verification; expired or exhausted codes return to request. Password
values never survive reload or enter URLs. Every form renders its CSRF token.
Cancellation invalidates the challenge. Saving signs all roles out; profile
responses use `no-store`. Reuse the profile card's existing colors, typography,
spacing and document scrolling; no separate OTP overlay or new visual tokens.

## Owner inventory oversight

The Owner Inventory parent groups exactly two primary destinations: editable
Products & prices and read-only Stock oversight. Oversight uses existing routes
for Inventory overview, Raw materials, Alerts, Expired, Replacement and Movements
as secondary views. Replacement shows requests and status, including converted
requests, with search, status filters, pagination and links to existing case details.
Batch/product/custody details remain readable through contextual links, with the
Inventory parent and Stock oversight selected. Preserve searches, filters and
pagination URLs and aggregate unread stock updates on the Stock oversight tab.
Reuse the shared Owner tab and table styling, native controls and server errors.
Stock mutations are hidden and independently blocked by backend capability and
repository guards. Product pricing retains its existing form and audit reason.
