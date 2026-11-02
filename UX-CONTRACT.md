# MEATTRACK dashboard UX contract

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
| Scrollbar | Native browser and existing `portal_base.css` shell | Existing portal shell | Natural document flow; bounded chart table | Responsive overflow checks |
| CRUD | Existing guarded portal destinations | Capabilities and action handlers | Dashboard navigation only | Isolated workflow regressions |

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
