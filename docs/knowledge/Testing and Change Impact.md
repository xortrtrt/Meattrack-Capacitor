---
tags: [meattrack, testing]
verified: 2026-10-30
---
# Testing and change impact

## Local Apache JMeter system verification (2026-11-02)

Installed JMeter 5.6.3 exercised the existing localhost public app and a separate
native instance using synthetic accounts in disposable PostgreSQL schemas. The
final serial functional run passed 274 HTTP samples and all post-run database
checks: paid-order fulfillment, completed replacement, unreserved unpaid checkout,
once-only message storage, preserved refund retry history, and expected stock.
Coverage also included all five roles, navigation/guards, CSRF/login throttling,
report exports, multipart evidence submission, and Inventory/Sales handoff.

Performance is not a clean pass. Initial public load recorded 3,861 samples with
11 host-aborted connections at 30 users; the 1/5/15-user stages had no failures.
A 30-user repeat passed 672 samples but had a 5.9-second p95. The authenticated
15-second pass recorded eight read timeouts and dependent session/CSRF failures.
With a longer timeout, serial Owner/Supervisor dashboards still took 17.6/24.6
seconds. These are development-machine observations, not production capacity or
a diagnosed root cause. Earlier harness expectation errors were corrected and
their raw artifacts retained separately.

Local report, JMX plans, JTL files, dashboards and reusable runner are under
`outputs/jmeter-system-2026-11-02/`; `REPORT.md` distinguishes each run. The runner
uses the existing isolated-schema/lifecycle fixtures, requires a loopback database
and a fresh output directory, and removes its temporary server/schema after use.
Existing business records were not targeted by write scenarios. Browser JavaScript,
external delivery/payment services, worker processing and the full pytest suite
were not verified by this task. See [[Architecture]], [[Roles and Access]],
[[Orders and Inventory]], and [[Returns and Financials]]. Sources:
`tests/test_inventory_postgres.py`, `tests/test_product_lifecycle.py`,
`app/portal_config.py`, and the local JMeter artifacts.

## Reseller catalog redesign (2026-11-02)

136 distinct focused checks passed: three new catalog browser/HTTP checks in
`tests/test_reseller_catalog_browser.py`, 120 portal-context checks, and 13 existing
catalog/cart/checkout/payment checks selected from `test_inventory_postgres.py`
and `test_portal_improvements.py`. Browser coverage verifies name/description
search, all categories and sort options, filtered pagination, empty results,
details values, expired-stock exclusion, image loading, quantity validation,
cart pricing, and unpaid stock remaining unreserved. Layout checks cover 320,
390, 560, 768, 1440, and 1920 pixels, including modal width and no page overflow.
HTTP checks retain session/role guards and authoritative cart stock validation.

The follow-up refinement passed 123 distinct checks: all three expanded catalog
checks and 120 portal-context checks. New assertions cover numbered navigation,
filter reset preserving sort, two-column phone browsing, quantity-button limits,
keyboard focus wrapping/restoration, unavailable-product focus, full image bounds,
and reachable Close with a long description. A final responsive-details recheck
passed after the phone image-size adjustment. JavaScript syntax and Ruff passed.
Reports: `outputs/reseller-catalog/refined-final.xml`, `refined-context.xml`, and
`refined-details-final.xml` in the same directory.

The final catalog run passed all three checks. Ruff, changed-template compilation,
and whitespace checks passed. Tests wrote only disposable PostgreSQL schemas;
the existing catalog records and payment/reservation rules were not changed.
Screenshots and JUnit reports are local artifacts under
`outputs/reseller-catalog/`. These results do not establish deployment.
See [[Orders and Inventory]] and [[Roles and Access]].

## Full Messenger refinement verification (2026-11-02)

The expanded `test_unified_contact_inbox.py` passed five cases, covering separate
and merged participants at desktop and phone widths. Checks include bottom
composer placement, collapsed/expanded cards, message badges without association
side effects, general replies without badges, failed-send draft/order/UUID retention,
overflow open/Escape behavior, grouped bubbles, and scroll preservation during
incoming replies. The compatibility run passed 130 portal-context, attachment,
replacement-role, and refund tests. This supersedes the earlier UI expectations
about top-mounted selectors and removed message links. JavaScript syntax, Ruff,
and diff checks passed; no backend or migration changes were required. Screenshots
and JUnit files are under `outputs/unified-inbox/`. See
[[Background Work and Integrations]].

## Order inquiry cards and category isolation (2026-11-02)

The revised `test_unified_contact_inbox.py` passed five cases, including desktop/
phone checks for both separate and merged direct participants. They verify scoped
order data, no default selection, post-send clearing, general replies without an
order, historical card opening, explicit inquiry deep links, System-only entries,
removed Show controls, and top-mounted selectors with an accessible composer.
The 120 portal-context checks and ten existing attachment/refund/replacement
browser checks also passed across separate focused runs. Ruff, targeted mypy,
and JavaScript syntax checks passed. Updated screenshots and JUnit results live
under `outputs/unified-inbox/`. No new schema migration was required. See
[[Background Work and Integrations]].

## Contact-grouped Inbox refinement (2026-11-02)

`tests/test_unified_contact_inbox.py` passed three focused cases: shared case
history with ownership/pair/retry checks, plus desktop and phone browser flows.
The browser checks cover one row for two cases, selection without navigation,
case-targeted replies, order-context sends, filtering, and composer visibility.
The updated `test_replacement_conversation_browser.py` passed both widths while
retaining staff role, retry, hidden-read, and read-only checks.

Separate compatibility runs passed 134 portal-context/replacement-workflow checks
and 13 attachment/refund/browser checks. The broader `test_portal_improvements.py`
run passed 19 tests but its unrelated legacy returns-layout assertion
`test_existing_return_details_keep_multiple_cases_selectable_and_default_closed`
still expects old `return-ID` dialog markup from the ongoing dedicated returns-page
changes. It was not changed to claim a clean full suite. Ruff, targeted mypy, and
JavaScript syntax checks passed. Local screenshots/JUnit output are under
`outputs/unified-inbox/`. See [[Background Work and Integrations]].

## Unified reseller Inbox verification (2026-11-02)

Focused checks passed across separate runs: `tests/test_portal_context.py` (120),
`tests/e2e/test_refund_workspace.py` (7),
`tests/test_portal_improvements_browser.py` (1), and
`tests/test_replacement_conversation_browser.py` (2). A timestamp serialization
failure introduced by the preview field was corrected before the replacement
browser recheck passed. The expanded browser checks cover categories, search by
return/case number, empty results, unread-summary display, switching conversation
types, collapsible prices, and composer visibility/no horizontal overflow at
390, 768, and 1440 pixels. Existing checks retain send retry, attachments,
refund deep links, role views, and declined-case read-only behavior.

Node syntax checks passed for both inbox/replacement scripts; Ruff passed for
the changed Python files and browser test, and mypy passed for the two changed
Python modules. Screenshots and the final browser/compatibility JUnit results are
local artifacts in `outputs/replacement-conversation/`. These checks used disposable
database schemas and do not establish deployment. See
[[Background Work and Integrations]] and [[Replacement Workflow]].

Choose verification from the behavior changed and the connections it touches.
Documentation setup does not establish that application tests or production pass.

| Change area | Useful existing coverage | Connected notes |
| --- | --- | --- |
| Auth/roles/session behavior | `test_system_hardening.py`, `test_owner_superuser.py`, `test_inventory_sales_separation.py` | [[Roles and Access]] |
| Stock and concurrent transactions | `test_inventory_postgres.py`, `test_database_concurrency.py`, `test_transaction_repairs.py` | [[Orders and Inventory]] |
| Return eligibility and evidence | `test_return_request_window.py`, `test_return_evidence_required.py` | [[Returns and Financials]] |
| Sales/Inventory handoff | `test_return_workflow_handoff.py`, `test_return_handoff_browser.py` | [[Returns and Financials]] |
| Refund custody and financial alignment | `test_refund_traceability.py`, `test_order_financial_alignment.py`, `test_sales_report_refunds.py` | [[Returns and Financials]], [[Database and Operations]] |
| Sales return tracking, processor refund navigation, review actions, and dashboard totals | `test_sales_refunds_returns_workspace.py`, `test_portal_context.py` | [[Returns and Financials]], [[Roles and Access]] |
| Complete-match/mismatch receipt decisions, fixed discarded holding, and replacement clearance | `test_inventory_receipt_decision.py`, `test_inventory_sales_separation.py`, `test_return_handoff_browser.py` | [[Returns and Financials]], [[Orders and Inventory]] |
| Personal activity reports, immutable documents, CSV/Excel/print and Owner access | `test_activity_reports.py`, `test_activity_report_routes.py`, `test_activity_reports_browser.py` | [[Sales Activity Reports]], [[Roles and Access]] |
| Expiry/lifecycle UI | `test_expired_stock_actions.py`, `test_product_lifecycle_browser.py` | [[Orders and Inventory]] |
| Forecasts/chat/email | `test_forecasting.py`, `test_live_chat_postgres.py`, `test_emailer.py` | [[Background Work and Integrations]] |
| Full portal journeys | `tests/e2e/` | [[Architecture]] |

The filenames in the first rows live under `tests/`. Read test fixtures and
markers before running browser or database suites. PostgreSQL tests use isolated
schemas; ordinary mocked tests cannot prove locking or trigger invariants.

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pytest -q -m postgres
.venv\Scripts\python.exe -m ruff check app tests tools
.venv\Scripts\python.exe -m mypy app
```

CI provisions PostgreSQL and browser dependencies, applies migrations, and checks
quality plus integration/browser behavior. Follow current CI definitions rather
than assuming a local shell has the same services or configuration. Record exact
commands and results when reporting a change.

Sources: [CI](../../.github/workflows/ci.yml), [fixtures](../../tests/conftest.py),
[test configuration](../../pytest.ini), [developer requirements](../../requirements-dev.txt),
[project configuration](../../pyproject.toml).

The portal upgrade adds `tests/test_portal_improvements.py` for payment boundaries,
concurrent stock claims, immutable reservation traces, processor handoff, media
privacy/read cursors, scoped refund OCR, and migration preservation. Container
validation has focused unit coverage in `tests/test_internal_conversation_media.py`.
`tests/test_portal_improvements_browser.py` renders all leader dashboards at desktop
and phone widths and exercises explicit conversation links, image/video uploads,
unread state, and collapsed order history against an isolated PostgreSQL schema.
Existing workflow tests use Supervisor/Owner for post-acceptance actions. See
[[Roles and Access]] and [[Database and Operations]] before upgrade verification.

On 2026-10-30, Ruff and mypy passed. The connected browser checks passed at 390 px
and 1440 px, including all three leader dashboards, private image/video messages,
unread behavior, collapsed order history, and keyboard-controlled return dialogs.
The payment-shortage regression also checks that the assigned Supervisor can inspect the original order payment proof
while an unrelated Supervisor cannot. Test output and screenshots are local build
artifacts under `outputs/portal-improvements/`, not customer evidence or deployment
proof.

The full PostgreSQL-backed run collected 810 tests: 779 passed, 18 skipped, and
13 failed against older role/UI expectations or test setup. After corrections,
all 13 failed cases plus the complete new workflow suite passed in a final
32-test recheck against another disposable schema. Both verification schemas were
removed. The aggregate latest result is 794 passed and 18 skipped across 812
distinct cases, including the two added regression cases. The skips require
external end-to-end service configuration. `full-verification.xml`,
`final-recheck.xml`, and `verification-summary.json` preserve the separate runs.

The 2026-10-30 database recovery fix passed 22 tests in
`test_database_concurrency.py` and `test_system_hardening.py`, plus Ruff and mypy.
The three new cases verify HTTP 503 during route, session-load, and session-save
connection failures, no leaked connection details or session cookie, and successful
requests after recovery. Both Compose definitions passed quiet configuration
validation. Local HTTP checks confirmed the home page, login, authenticated-route
login redirect, and database health probe responded successfully with session
creation working. See [[Database and Operations]] for startup/recovery handling.

The subsequent conversation/history refinements add
`tests/test_conversation_order_receipts.py`: owning-reseller acknowledgement,
concurrent once-only recording, immutable receipts, unchanged stock and return
deadlines, participant photo metadata, and report section order. The connected
browser coverage checks individual attachment removal, image/video modals,
Enter/Shift+Enter, attachment-only rendering, explicit conversation selection,
photo/icon fallbacks, and refund spacing at phone and desktop widths. The activity
report browser checks the Orders–Sales–Returns–Refunds tab order, and the reseller
history browser retains collapsed defaults under the Completed label. Sources:
`tests/test_portal_improvements_browser.py`, `tests/test_activity_reports_browser.py`,
`tests/test_reseller_returns_tab.py`, and migration 043. Local targeted runs on
2026-10-30 passed; see `conversation-receipts-verification.xml`,
`report-history-ux-verification.xml`, and `conversation-ux-final.xml` under
`outputs/portal-improvements/`.


## Owner portal overhaul, 2026-10-30

See [[Owner Portal]] for the nine-destination navigation map, business measures,
contextual tabs, exports, and staff handover. Verification used current local
source; it is not production deployment evidence.

- Full non-PostgreSQL/non-browser suite: **553 passed**.
- Existing Owner pages/exports, activity reporting, report-refund alignment,
  customer follow-ups, Sales/Inventory handoff, and receipt-decision PostgreSQL
  checks: **58 passed**.
- New isolated-schema checks in `tests/test_owner_overhaul.py`: **6 passed**.
  They cover return-processing handover and guards, completed replacements with
  open follow-ups, explicit takeover of in-progress refunds, Owner fallback scope,
  full-catalog stock/expiry totals, and Sales-only summary export scope.
- Final browser check in that module: **1 passed**. It traverses all 19 ordinary
  Owner workspaces at 1440, 768, and 390 pixels, verifies nine main links and parent
  selection, checks horizontal overflow, chart/table synchronization, account
  dialog dismissal, mobile navigation, and persistence of the Owner return scope
  during status filtering. No browser script errors were recorded.
- After the final shared return-template refinement, **153 affected non-PostgreSQL
  tests passed again**; these overlap the full suite and are not additional cases.
- Ruff passed for `app tests tools`; mypy passed for all 73 application source
  files; `git diff --check` found no whitespace errors.

The aggregate is **618 distinct passing cases across separate runs**. This is not
an assertion that the entire PostgreSQL/browser suite ran in this task. All writes
in the new tests use disposable schemas, removed by fixture teardown. Browser
screenshots use synthetic fixtures and are local artifacts under
`outputs/owner-overhaul/`. Final new-check reports are `backend-verification.xml`
and `browser-verification.xml` in that directory. Earlier failed browser attempts
were corrected and superseded by the final passing browser report.

Commands:

```powershell
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider -m 'not postgres and not e2e'
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_owner_superuser.py tests/test_activity_reports.py tests/test_sales_report_refunds.py tests/test_return_customer.py tests/test_return_workflow_handoff.py tests/test_inventory_receipt_decision.py -m 'postgres and not e2e'
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_owner_overhaul.py -m 'not e2e' --junitxml=outputs/owner-overhaul/backend-verification.xml
.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_owner_overhaul.py -m e2e --junitxml=outputs/owner-overhaul/browser-verification.xml
.venv\Scripts\python.exe -m ruff check app tests tools
.venv\Scripts\python.exe -m mypy app
git diff --check
```


## Version-2 replacement workflow, 2026-10-30

[[Replacement Workflow]] introduces new-request-only comparisons, direct Supervisor
handoff/private conversation, complete receipt before modal top-up payment, funds
verification before FEFO preparation, separate release/delivery, and difference
refund settlement. Existing case versions retain their original workflow.

Verification against the current local source:

- Full non-PostgreSQL/non-browser suite: **553 passed**.
- Targeted backend suite across the new workflow, earlier return handoff, complete
  receipt decisions, refund/report alignment, activity reports, and Owner handover:
  **46 passed**. This includes **10 new replacement regressions**, covering price
  components, direct assignment/conversation permissions and retries, receipt/pay/
  preparation/release/delivery sequencing, lower-price refunds, expiry/restocking,
  Owner fallback, explicit funds confirmation, version-1 preservation, migration
  replay, and immutable conversation history.
- New full browser journey: **2 passed**, at 1440 and 390 pixels. Both submit a
  real request in a disposable schema, review the comparison, send a linked case
  message, approve as Supervisor, confirm Inventory receipt, upload/confirm a mocked
  OCR receipt in the Returns modal, verify funds, prepare/release FEFO stock, and
  confirm delivery. The final phone rerun also passed after tightening visible-
  message read tracking. These tests send no real funds or external messages.
- Read-only current local Owner page/export checks: **17 passed** after migration.
- The multiline-original-price matching recheck passed. Ruff passed for `app tests
  tools`; mypy passed for 75 application files; `git diff --check` found no
  whitespace errors.

The runs are separate and rechecks overlap; they are not a claim that the full
PostgreSQL/browser suite ran. New data-writing tests use disposable schemas that
are removed by their fixtures. Test reports and synthetic screenshots are local
artifacts under `outputs/replacement-workflow/`, including `unit-verification.xml`,
`backend-verification.xml`, `browser-verification.xml`, `local-owner-smoke.xml`,
`pricing-recheck.xml`, and `final-phone-verification.xml`. Earlier failed browser
attempts were corrected before these final passing reports.

Migration 044 was applied to the verified loopback development database while
local app/worker writes were paused. Its stored checksum matches the source;
app and worker resumed, and the local login page returned HTTP 200. This does
not establish a production deployment. See [[Database and Operations]].


The final replacement regression module passed **11 tests** after preserving
Supervisor mismatch-quantity revisions: original delivery/claim bounds are checked,
credit is recalculated, a new comparison is frozen, and reseller acceptance is
required before preparation. Ten of these cases overlap the earlier 46-test run.
`final-replacement-regressions.xml` records this latest run. The Supervisor-facing
approval explanation recheck also passed (`final-event-verification.xml`).


## Replacement conversation redesign, 2026-10-31

[[Replacement Workflow]] now uses the current Sales/reseller messenger's list,
participant photo, incoming/outgoing/system bubble, and composer presentation.
Desktop has a list/thread split; phones switch between the thread and case list.
The case strip links back to return details. Payment and evidence remain in their
existing Returns workflow. Case unread counts returned by message/read endpoints
keep the active row current without reading a hidden mobile thread.

Verification against local source (disposable PostgreSQL schemas, synthetic data):

- **4 browser tests passed**, covering the redesigned messenger at 1440/390 pixels
  and the complete replacement request/approval/receipt/payment/FEFO preparation/
  release/delivery journey at those widths. Presentation checks include Supervisor,
  reseller, and Owner views; participant photo cropping; incoming/outgoing/system
  styling; phone list switching and focus; hidden-thread unread counts; safe UUID
  send retries and draft retention; Shift+Enter; long literal text wrapping; normal
  message-index access; declined-case read-only state; and no browser script errors.
- **12 workflow backend tests passed**, including a new unread-count regression
  for own replies, partial reads, stale cursors, and unrelated-case message IDs.
- **553 non-PostgreSQL/non-browser tests passed** (`unit-verification.xml`).
- Ruff passed for `app tests tools`; mypy passed for 75 application source files.
  `git diff --check` found no whitespace errors.

Browser reports and synthetic screenshots are under
`outputs/replacement-conversation/`: `browser-verification.xml`,
`backend-verification.xml`, `supervisor-1440.png`, `supervisor-390.png`,
`reseller-1440.png`, `reseller-390.png`, and `conversation-list-390.png`.
Earlier attempts caught and corrected an unnecessary raw datetime in thread
metadata, a hidden-element test selector, and a fixed future payment time in the
existing browser fixture. These were resolved before the final passing browser run.
No migration or production deployment was required or performed.

Sources: `tests/test_replacement_conversation_browser.py`,
`tests/test_replacement_workflow_browser.py`, `tests/test_replacement_workflow.py`,
`app/repositories/replacement_messages.py`, `app/portals/product_returns.py`,
`app/templates/portals/_replacement_conversation.html`,
`app/static/css/replacement_workflow.css`, `app/static/js/replacement_workflow.js`.
See [[Background Work and Integrations]].


## Local Owner page rendering, 2026-11-01

Corrected malformed Jinja syntax in
`app/templates/portals/owner/_workspace_header.html` that caused HTTP 500 after
Owner sign-in. The active-tab accessibility attribute now uses a quoted template
expression. All application templates compiled; **47 Owner-focused tests passed**.
Authenticated requests to the running local dashboard, orders, returns, inventory,
accounts, and profile pages each returned **HTTP 200**. The dashboard tab strip
also identified exactly one current page. Temporary verification sessions were
removed. No business or permission rules changed; this fix was kept local.
See [[Owner Portal]]. Report: `outputs/owner-local-fix/verification.xml`.


## Local Wi-Fi sharing, 2026-11-01

Validated the optional app bind in `compose.yml` with `docker compose config` and
recreated only the local app. Host-side health and sign-in-page requests passed
through both loopback and the host's Wi-Fi address. Confirmed PostgreSQL and
mail-capture remained loopback-bound and the existing Windows Docker Desktop
Backend TCP allowance covered the active network profile. Also validated that
an explicit loopback override restores the original binding. A request from the
friend's separate device has not been observed. See [[Database and Operations]].

## Owner alerts and notification overlay, 2026-11-01

18 Owner directory regression tests and two unread-badge tests passed. Authenticated
Playwright checks on the running local Alerts page passed at 1440, 768, and 390px:
HTTP 200, no horizontal overflow, no JavaScript errors, popup visible in the top
layer and receiving pointer hits above page content, and Escape dismissal.
Temporary verification sessions were deleted. Screenshots are local under
`outputs/owner-alerts/`. These checks do not establish production deployment.
See [[Owner Portal]].

The subsequent visual redesign was inspected in rendered desktop and phone
screenshots. Authenticated browser checks passed at 1440, 960, 768, 390, and 320px,
including the warning-level GET form at zero packs, no horizontal overflow, no
JavaScript errors, and notification top-layer visibility/Escape dismissal.
Template branch checks covered empty worklists, expiry today/tomorrow, and
collapsed resolved history without active-case buttons. No alert statuses or
inventory quantities were changed by these checks. Styling is isolated in
`app/static/css/portals/owner-alerts.css`.

## Owner alert workflow, 2026-11-01

Completed 18 alert-workflow cases in `tests/test_owner_stock_alerts.py`, including
per-Owner review isolation/idempotence, unchanged notification reads, audit rollback,
active-risk closure rejection, reason validation, HTTP settings persistence,
Owner/CSRF checks, threshold boundaries, paid-order and replacement reservations,
recurrence, escalation, notification deduplication, concurrent scans, physical
partial/full pull-outs, quality reclassification, migration consolidation, and
legacy raw-material handling. Also passed 60 Owner/navigation tests and 42 stock,
return-custody, expiry and Owner-overhaul regressions across targeted runs.

All Jinja templates compiled; focused Ruff checks passed. Authenticated browser
checks passed at 1440, 960, 768, 390, and 320px: read-only Alerts rendering, saved
policy link, absence of routine Resolve buttons, no horizontal overflow/JavaScript
errors, and notification top-layer visibility/Escape dismissal. Desktop screenshots
were visually inspected; earlier phone layout checks remain valid.

Applied migration 045 through the migration runner to the existing local database;
repeat run reported up to date. Baseline-backed isolated test schemas and migration
replay/consolidation passed. Restarted local app/worker and verified a successful
stock-alert reconciliation timestamp; both services remained running and the app
reported healthy. Tests used isolated schemas; temporary browser sessions were
removed. No production deployment was performed. See [[Owner Portal]],
[[Orders and Inventory]], and [[Background Work and Integrations]].

## Inventory/Owner alert connection, 2026-11-01

Passed 177 targeted tests: five shared-workspace PostgreSQL cases, 18 Owner alert
workflow cases, 112 portal-context tests, and 42 Owner/navigation cases. Coverage
includes matching live stock/thresholds/episode states, private Owner reviews,
legacy/raw-material scope, forecast exclusion, read-only GETs, Inventory/Sales/
Supervisor access, dashboard counts before worker scans, physical pull-out
propagation, and new/legacy stock notification redirects with read state.

Authenticated browser checks passed for both portals at 1440, 960, 768, 390, and
320px, including overflow, scoped links/controls, no JavaScript errors, and
notification popup visibility/hit-testing/Escape. Inventory desktop and phone
screenshots were visually inspected (`outputs/inventory-alerts/`). Temporary browser
sessions were deleted. Focused Ruff and diff checks passed. No new migration was
needed; the local worker was restarted to load new stock notification targets.
This does not establish production deployment. See [[Owner Portal]],
[[Orders and Inventory]], and [[Roles and Access]].

Inventory Alerts layout polish, 2026-11-01: inspected fresh desktop and phone
screenshots, preserving the existing palette/summary structure. Browser checks
passed at 320, 390, 768, 960, and 1440px for HTTP 200, overflow, Owner-only control
absence, notification hit-testing/dismissal, and JavaScript errors. Changes are
scoped to Inventory presentation; no stock rules changed. See [[Owner Portal]].

## Inventory alert category details, 2026-11-01

Six shared Inventory alert tests passed, including browser clicks through all three
summary cards and Back links at 1440, 768, 390, and 320px. Verified category item
counts against the live workspace, batch filtering, explicit inclusion copy,
category-specific empty state, invalid-category 404, Sales denial, read-only GETs,
no horizontal overflow, and no JavaScript errors. Desktop and phone screenshots
were visually inspected; artifacts are under `outputs/inventory-alert-details/`.
All 94 Jinja templates compiled; focused Ruff checks passed. Tests used isolated
PostgreSQL schemas, and no production deployment was performed. See
[[Orders and Inventory]]. Source: `tests/test_inventory_alert_workspace.py`.

Nine Owner regression tests also passed, including the existing full Owner portal
browser sweep at desktop, tablet, and phone widths. Shared Owner alert rendering
remains covered by that sweep.

Inventory alert visual simplification, 2026-11-01: six shared alert tests passed
again after the ledger-theme redesign. Browser coverage includes category cards,
current-category navigation, Back links, matching item counts, empty state, access
restrictions, and overflow/JavaScript checks at 1440, 768, 390, and 320px. Final
desktop and phone screenshots were inspected. Long detail explanations were
replaced by short labels; no stock rules changed. All 94 templates compiled;
focused Ruff and whitespace checks passed. See [[Orders and Inventory]].

## Stock Alerts and Pull-outs connection, 2026-11-01

Three connection tests passed, including browser journeys at 1440 and 390px:
expired alert to the exact pending batch, overriding a previously selected
Disposed tab; partial and complete physical pull-outs; preserved batch context;
live warehouse/holding balances; holding-tab navigation; and return to the cleared
expired-alert list. The HTTP/PostgreSQL case also covers actionability before the
worker scan, read-only GETs, invalid batches, over-pull-out errors, idempotent
replays, Sales denial, and worker episode closure only after warehouse clearance.
Ten existing alert, pull-out destination/CSRF, and action regression tests passed.
Desktop and phone screenshots were inspected under
`outputs/alert-pullout-connection/`. No overflow or browser script errors were
observed. All 94 templates compiled; focused Ruff and whitespace checks passed.
Tests used isolated schemas; no production deployment was performed. See
[[Orders and Inventory]] and [[Decisions]]. Source:
`tests/test_alert_pullout_connection.py`.

An additional 28 pending-pull-out log and product lifecycle regressions passed,
including partial/idempotent removal and disposal, expiry, and stock history.

## Owner alert card destinations, 2026-11-01

Both portal card-navigation browser cases passed at 1440, 768, 390, and 320px,
covering all three cards, matching category contents/counts, current-category tabs,
Back links, empty states, invalid categories, role denial, read-only page visits,
and no overflow or browser script errors. Owner desktop and phone screenshots
were inspected in `outputs/owner-alert-card-details/`. All 18 Owner stock-alert
workflow regressions passed. All 95 templates compiled; focused Ruff and whitespace
checks passed. No production deployment was performed. See [[Owner Portal]] and
[[Orders and Inventory]]. Source: `tests/test_inventory_alert_workspace.py`.


Inventory tools coverage (2026-11-01): `tests/test_inventory_tools.py` verifies
concurrent deductions/dispatch, precision, replay identity, reservation protection,
expiry boundaries, immutable standards, multi-component rollback, batch archival,
photo validation/storage, role guards, and baseline-to-upgrade stock preservation.
`tests/test_inventory_tools_browser.py` uses real templates/JavaScript and an
isolated PostgreSQL schema to exercise desktop/mobile deductions, standard package
setup/dispatch, photo preview/upload, and batch removal. Screenshots and run logs
are under ignored `outputs/inventory-tools/`. See [[Orders and Inventory]].

Validation in this session: 558 non-PostgreSQL/non-e2e unit tests passed; the new
stock-tool tests and desktop/mobile browser flow passed. Existing stock/alert
regressions produced 56 passes and an initial baseline-replay failure; migration
046 was made replay-safe and that failed check passed on rerun. The separate
pre-046 upgrade/preservation check passed. Ruff, mypy, JavaScript syntax checking,
and diff whitespace checks passed. This is local validation, not production proof.

## Payment review consistency, 2026-11-02

Eight receipt browser tests passed, including mobile replacement receipt review
and submission. Replacement workflow and portal-improvement database regressions
passed, including stored reseller values overriding posted staff values and legacy
pending-order payment/reservation. The legacy fixture explicitly bypasses current
order triggers only to create a historical unpaid row in its isolated schema;
submission then runs with triggers enabled. Portal context tests passed after
updating the old plain-upload expectations; an added read-only review rendering
check also passed. All templates compiled. Focused Ruff, JavaScript syntax, and
diff whitespace checks passed. Mobile screenshot inspected at
`outputs/payment-review-mobile.png`. No deployment performed. See
[[Replacement Workflow]] and [[Orders and Inventory]].

Return layout correction (2026-11-02): inspected the live reseller return dialog
in Chrome before and after changes, opened the comparison dropdown, and checked
the 390px layout. Reference cards collapse by default; timeline notes expand
separately. All 114 portal context tests passed and all templates compiled.
Supervisor/Owner presentation shares the updated components; its separate
authenticated browser window was not inspected. Desktop capture:
`outputs/return-layout-desktop.jpg`. See [[Returns and Financials]].

Supervisor older-case correction (2026-11-02): the actual authenticated
`/portal/sales-supervisor/returns?case=1` page was inspected before/after changes,
including request/quote and Inventory-record dropdowns and a 390px mobile view.
No real payment verification was submitted. Case proof without reseller metadata
now shows a disabled awaiting-submission state. All 120 portal-context tests
passed, covering both receipt-scan success contracts, read-only versions, missing
metadata, and staff-posted values. Existing lifecycle regressions passed; the new
older-case stored-payment regression passed after correcting its expected reference
to the existing uppercase normalization. Both focused version-2 payment regressions
passed. Ruff, template compilation, and diff whitespace checks passed. Screenshots:
`outputs/supervisor-return-1-desktop.jpg`, `outputs/supervisor-return-1-mobile.jpg`.
See [[Replacement Workflow]].

Supervisor overview validation (2026-11-02): 127 summary/portal-context checks passed.
The seven overview checks cover visible decision facts, terminal/mismatch status,
current reseller receipt details, refund presentation, and escaped expandable
customer reasons. The live authenticated case was inspected at desktop and 390px;
no case action was submitted. Captures are `outputs/supervisor-case-overview-desktop.jpg`
and `outputs/supervisor-case-overview-mobile.jpg`. See [[Returns and Financials]].

## Release audit, 2026-11-02

Aggregate initial runs and final targeted rechecks produced **910 distinct passing
checks, zero unresolved failures, and zero remaining skips**. The initial broad
PostgreSQL run had 294 passes, eight browser/UI expectation failures, and eight
external-service skips. Final rechecks passed all eight failing paths after updating
comparison-review, collapsed-card, status-summary, and action-label expectations.
The separately configured browser server covered the external skips in a disposable
schema; its sidebar-scroll expectation was corrected to use available scroll space.
The role-navigation audit rendered all configured role menus and key case/product
pages against an isolated PostgreSQL schema without server errors.

Confirmed gaps fixed during audit: payment queue readiness for current reseller
metadata, version-2 payment review before stock reservation, preserved follow-up
work, and stale payment-review rejection under the case lock. Older reservation
requirements remain reflected in the displayed responsible party. Unit, integration,
and browser checks cover these changes. Ruff, mypy (78 source files), Python
compilation, JavaScript syntax, all-template compilation, importer safety self-check,
and diff whitespace checks passed. Both Compose definitions validate; production
validation used placeholder secrets. All 46 local migrations are installed; migration
023 uses an explicitly supported historical checksum. Recent local app/worker logs
showed no new server errors; earlier startup retries had recovered.

The aggregate is recorded in ignored `outputs/audit-summary.json`; detailed JUnit
reports are `outputs/audit-*.xml`. Test schemas and the isolated browser server were
cleaned up. These results verify local source and test behavior. See
[[Replacement Workflow]], [[Orders and Inventory]], and [[Database and Operations]].

Return-request interaction correction (2026-11-02): 43 focused PostgreSQL/browser
checks passed across the request-options/lifecycle suite (7) and replacement,
history, receipt, eligibility-window, and portal regressions (36). Updated browser
checks exercise missing quantities/details/photos, catalog search, failed comparison
retry, comparison invalidation after quantity edits, visible mobile footer buttons,
and successful replacement submission through delivery. The live authenticated
history form was inspected on desktop and phone; only price previews were requested,
without submitting a real return. Screenshots are in ignored
`outputs/return-request-desktop.jpg` and `outputs/return-request-mobile.jpg`.
Ruff, JavaScript syntax, all-template compilation, and whitespace checks passed.
Reports: `outputs/return-request-options-final.xml` and
`outputs/return-request-regressions.xml`. See [[Sales and Reseller Returns]].

Sales case cards (2026-11-02): 134 distinct focused checks passed across initial
and final rechecks. Nine presentation regressions cover visible facts, role
boundaries, workflow-version step order, terminal states, payment progression,
parallel delivery/refund work, and escaped customer text. Existing portal context,
replacement, customer-action, and refund/handoff browser checks passed after
replacing obsolete dropdown clicks and counting submit actions separately from
the new Close button. Actual case 3 was inspected on desktop and phone: no
disclosures or horizontal overflow, and Close/reopen preserves the All queue.
No live case decision was submitted. All templates compile; focused Ruff and
whitespace checks passed. Ignored artifacts: `outputs/sales-case-cards.xml`,
`outputs/sales-case-actions.xml`, `outputs/sales-case-final.xml`, and
`outputs/sales-case-3-{desktop,mobile}.jpg`. See [[Sales and Reseller Returns]].

Shared return timeline (2026-11-02): 182 distinct focused checks passed across
initial runs and final rechecks. Fourteen new presenter tests cover payment
submission versus verification, current receipt metadata, both stock/payment
orders, Inventory privacy and physical completion, mismatch/terminal behavior,
recorded dates, and concurrent delivery/refund work. Existing portal context,
Inventory separation, replacement browser, customer actions, history, Sales cards,
and Supervisor facts passed after replacing prose expectations with milestone
state assertions. One summary fixture now explicitly records its accepted quote.
Desktop/phone browser checks cover Sales, Reseller, and Inventory timeline states;
the live Sales dialog was visually inspected at both sizes without case mutations.
Ruff, mypy (79 files), all-template compilation, and whitespace checks passed.
Ignored reports: `outputs/shared-return-timeline.xml`,
`outputs/return-timeline-followup.xml`, `outputs/return-timeline-final.xml`,
`outputs/return-timeline-summary-final.xml`. Captures:
`outputs/shared-return-timeline-{desktop,mobile}.jpg`.
See [[Sales and Reseller Returns]] and [[Replacement Workflow]].

Supervisor dedicated page (2026-11-02): 160 distinct focused checks passed across
initial runs and final targeted rechecks. Coverage includes context rendering,
responsive Sales/Owner/Inventory pages, full replacement approval/payment/delivery,
follow-up resolution, legacy clearance, empty/error/completed states, and the
five-stage mapping. New browser coverage verifies dashboard filter preservation,
keyboard tabs, photo preview, required rejection fields, confirmation cancellation,
actual rejection, alternative-product approval with authoritative repricing, and
explicit revision of processed requests. Test data lives only in disposable schemas.
The actual authenticated Supervisor case page was inspected on desktop and mobile;
no live approval, rejection, transfer, or case mutation was submitted.
Ruff, mypy (79 files), template compilation, JavaScript syntax, and whitespace checks
passed. Ignored reports: `outputs/supervisor-page-final-unit.xml`,
`outputs/supervisor-page-final-browser.xml`, `outputs/supervisor-page-offer-final.xml`,
`outputs/supervisor-page-states.xml`, `outputs/supervisor-details-page.xml`.
See [[Sales and Reseller Returns]].


Reseller dedicated page (2026-11-02): 191 distinct focused checks passed across
final unit/context/receipt runs and browser rechecks. Coverage includes the five
tabs, public role projection, current-revision acceptance and cancellation,
legacy image-only resubmission, recognition and confirmation gates, replacement
payment/funds/delivery flow, optional acknowledgment, follow-ups, history navigation,
keyboard tabs, photo preview, and responsive layouts. Browser actions used disposable
schemas; the actual authenticated reseller page was inspected at desktop and phone
sizes with no live acceptance, payment, acknowledgment, or message submitted.
Ruff, mypy (79 files), template compilation, JavaScript syntax, and whitespace
checks passed. Ignored reports: `outputs/reseller-details-checks-final.xml`,
`outputs/reseller-details-browser-final.xml`, `outputs/reseller-details-flow-final.xml`.
Captures include
`outputs/reseller-returns-tab/track-return-{375,768,1440}.png` and
`outputs/reseller-details-page-desktop.png`. See [[Sales and Reseller Returns]].


Return activity layout correction (2026-11-02): 16 focused history-browser and
Reseller/Supervisor presentation checks passed. Actual reseller Activity History
was inspected on desktop and phone: matching timeline/event cards, no horizontal
overflow, and the action bar fully inside the viewport. Changed templates compile;
whitespace checks passed. Report: `outputs/return-activity-layout.xml`.


Conversation button design (2026-11-02): 17 focused presentation and connected
browser checks passed, plus a targeted phone recheck using the new Supervisor
footer button to open its case conversation. Both dedicated pages use the same
shared link component and existing role-specific destinations. Desktop and phone
captures were inspected using disposable test records. No live messages were sent.
Templates compile; focused Ruff and whitespace checks passed. Reports:
`outputs/return-conversation-button.xml`, `outputs/return-conversation-navigation.xml`.


## Role-dashboard verification (2026-11-02)

[[Role Dashboards]] uses real templates with disposable PostgreSQL schemas.
Focused runs recorded 128 passing domain/context checks, 11 passing final
role-dashboard domain/browser checks, 11 connected alert/conversation checks,
and 30 replacement/Owner/inventory/catalog/cart/checkout regressions. These overlap;
there are 172 distinct passing cases across these runs, not a full-suite result.
Ignored JUnit reports and the exact commands/results are in
`outputs/role-dashboards/VERIFICATION.md` and its linked XML files.

Browser coverage spans all five roles at 320/390/768/1024/1440/1920px, DPR-2
zoom-equivalent reflow, zero queues, keyboard disclosures, chart data, destination
links, image loads and missing-contact/long-content states. No console errors,
failed HTTP requests or horizontal document overflow occurred in the final run.
Screenshots show disposable fixture data, not live customer records. Manual
browser zoom, physical-device and screen-reader verification were not performed.
Ruff, selected mypy, fifteen-template compilation, token mapping and whitespace
checks passed. DESIGN lint had zero errors and six unused-token warnings. The
Premium strict audit script became unavailable in its loaded cache path; separate
targeted static checks do not claim to replace it. No deployment was performed.
