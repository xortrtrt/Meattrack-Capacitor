---
tags: [meattrack, testing]
verified: 2026-10-30
---
# Testing and change impact

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
