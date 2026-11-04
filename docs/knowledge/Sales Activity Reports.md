---
tags: [meattrack, reports, sales]
verified: 2026-10-30
---
# Sales activity reports

Sales prepares one combined report for the Owner: Orders, Sales, Returns and
Refunds. Reseller-submitted reports are excluded. The report workspace has
Prepare report and Submitted reports views, with preview, optional overall and
section notes, CSV by section, a five-sheet Excel workbook, and an A4 print view.
There is no cumulative financial totals panel on the Sales report page.
The preview tabs, print sections, CSV choices, and Excel sheets follow that order
(after Summary where present). Orders is the initial preview tab. This changes
presentation without rewriting submitted report values or attribution rules.
Checked 2026-10-30 against `app/report_documents.py`,
`app/repositories/activity_reports.py`, `app/templates/portals/reports/`,
`app/templates/portals/team-leader/reports.html`, and
`tests/test_activity_reports_browser.py`.

## Selection and attribution

The selected dates apply to the **original order creation date**, localized to
Asia/Manila, across every section. Later return/refund work on an earlier order
belongs to a regenerated report for that original order period.

An assigned reseller alone does not qualify a record. Orders require a recorded
personal counter-sale creation, approval, rejection or fulfillment. Sales include
only those personally handled orders which are fulfilled. Return reviews,
rejections, settlement authorization and additional-payment verification qualify
the Returns section, without claiming the original sale. Current source access
is also enforced; unknown actor attribution is not guessed.

Refunds include personal refund requests/actions and outcomes linked to personally
handled return cases. The actual transfer processor is named independently of
the report author. This does not grant Sales transfer-processing permissions.
See [[Roles and Access]] and [[Returns and Financials]].

Money is aggregated once per order before product/action details. Only completed
refunds against fulfilled sales reduce retained sales. Cancellation refunds,
unsettled obligations and return credits have separate meanings; replacement
releases/top-ups are not new ordinary sales.

## Preview and submission

One server builder supplies the preview and all documents. A signed, account-bound
preview token expires after one hour. Submission rebuilds the source data in a
consistent transaction and rejects a stale preview, retaining notes for review.
A unique submission key makes repeated submissions idempotent.

Migration 037 adds `sales_report_activity_snapshots`, linked to `sales_reports`.
Its versioned JSON document freezes names, values, statuses, actions and notes;
the table rejects updates/deletes/truncation. New submitted views and exports
read this snapshot, including the Owner's aggregate CSV. Existing reports retain
their legacy snapshot/late-adjustment behavior and do not gain invented activity
details. The author and Owner can view/download a new report; other Sales accounts,
Resellers and Inventory cannot. Submission records an in-portal Owner notification.

CSV protects text from formula interpretation and carries report metadata per row.
Excel keeps amounts numeric and configures headers, filters, wrapping and printing.
The document print view includes every section, independent of preview tabs.

Sources: `app/repositories/activity_reports.py`, `app/portals/activity_reports.py`,
`app/report_documents.py`, `app/repositories/reporting.py`,
`app/repositories/superuser.py`, `app/templates/portals/reports/`,
`app/templates/portals/team-leader/reports.html`, and
`database/migrations/037_sales_activity_reports.sql`.
Related: [[Architecture]], [[Database and Operations]], [[Testing and Change Impact]].

The 2026-10-30 [[Owner Portal]] gives submitted reports a dedicated inbox; cumulative
business financial context has moved to Overview. The Owner summary CSV is explicitly
an all-Sales-record export, independent of page filters, restricted to team-leader
report sources. New saved documents and legacy financial behavior are preserved.
Sources: `app/portals/owner.py`, `app/repositories/superuser.py`,
`app/templates/portals/owner/reports.html`.

Supervisor `replacement_delivered` events qualify as personal return work for
version-2 [[Replacement Workflow]], without claiming an ordinary sale or altering
already saved report snapshots. Source: `app/repositories/activity_reports.py`.

## Sales visibility by current assignment (2026-11-04)

Sales Leader and Supervisor order lists, counts, sales totals, purchase summaries,
and newly prepared Sales sections use the reseller's current
`resellers.team_leader_account_id`, rather than the historical leader stored on
the order. Reassignment removes those sales from the former assignee's reads.
Owner queries retain business-wide access. Personal action attribution remains
required for activity reports; submitted snapshots remain immutable. Separately
assigned return/refund processing permissions are unchanged.
Sources checked: `app/repositories/orders.py`, `reporting.py`,
`report_settlements.py`, `activity_reports.py`, and `portal_dashboard.py`
(all under `app/repositories/`). See [[Roles and Access]],
[[Sales Activity Reports]], and [[Decisions]].
