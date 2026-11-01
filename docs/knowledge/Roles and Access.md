---
tags: [meattrack, permissions]
verified: 2026-10-30
---
# Roles and access

Portal navigation describes available screens; server-side capabilities and
ownership-scoped queries enforce access. A hidden button is not an authorization
boundary. Sales supervisor is a portal role backed by the team-leader account
type and a specialized team-leader role.

| Actor | Main responsibilities | Connections |
| --- | --- | --- |
| Public visitor | Catalog, FAQ, inquiry, live prospect chat | [[Background Work and Integrations]] |
| Reseller | Own cart/orders, payment proof, own return cases | [[Orders and Inventory]], [[Returns and Financials]] |
| Sales team leader | Assigned reseller work, order/payment review, initial return acceptance/rejection and assigned-case tracking | Ownership affects evidence, messaging, reports |
| Sales supervisor | Supervisor-scoped onboarding, orders, accepted return processing, refunds | Refund permission differs from sales visibility |
| Inventory team leader | Production, stock, expiry, physical return receipt/verification, replacement release | Physical quantities and custody |
| Owner | Broad oversight, accounts, pricing, operations, recovery and settings | Actions require authenticated capabilities and audit records |

Return custody and financial processing are separate responsibilities. Inventory
views focus on stock and quantities. The assigned Sales Leader accepts or rejects
new requests; Supervisor/Owner manages accepted resolutions and reported
mismatches. Confirming a complete matching receipt clears the recorded approval
in one Inventory action. Authorized Supervisor/Owner processors handle transfers.
Assigned leaders can inspect public progress and settlement summaries. Direct
evidence-download endpoints preserve processor and participant permissions.

Sales Leaders use **Returns & tracking** at `/portal/team-leader/returns` for
initial decisions and assigned reseller tracking. Acceptance transfers further
Sales work to a named Supervisor, with Owner fallback. After handoff, assigned
leaders cannot revise offers, authorize settlement, verify additional payment, or
resolve follow-ups. Their tracking view excludes processor remarks and payment
attachments; refund-transfer proof downloads are restricted to processors and the
reseller. Legacy Sales `/refunds` links redirect into tracking and expose no money
processing tab. Inventory's custody and release permissions remain separate.

Owner portal actions no longer require a separate emailed step-up code. Existing
sessions, capabilities, CSRF, required reasons, and audit records remain enforced.
Activation, login verification settings, password-change, and recovery flows are
unchanged. Checked against `app/capabilities.py`, `app/portals/owner.py`,
`app/portals/refunds.py`, and `app/repositories/product_returns.py` on 2026-10-29.
See [[Returns and Financials]] and [[Decisions]].

Sessions are backed by PostgreSQL. Login/activation/password-change handling,
session rotation, CSRF enforcement, and rate controls connect authorization to
[[Architecture]]. Check configuration and implementation when changing defaults;
see the documented drift in [[Questions and Contradictions]].

Sources: [capabilities](../../app/capabilities.py),
[portal dependencies](../../app/portal_dependencies.py), [navigation](../../app/portal_config.py),
[authentication](../../app/routers/auth.py), [sessions](../../app/web_sessions.py),
[return process](../refund-return-process.md), [owner actions](../../app/portals/owner.py).

The 2026-10-30 [[Owner Portal]] has nine primary destinations; detailed workspaces
keep their URLs, capabilities, and contextual tabs. Deactivation and removal of
Supervisor access include accepted returns/open replacement follow-ups and actual
refund-processing responsibility. Transfers carrying return/refund duties require
an active Supervisor destination. In-progress transfer attempts require completion,
failure, or the existing Owner takeover first. Assignment transfer preserves the
actual processor. Sources: `app/repositories/superuser.py`,
`app/repositories/accounts.py`, `tests/test_owner_overhaul.py`.

Version-2 [[Replacement Workflow]] requests go directly to a Supervisor on
submission, rather than awaiting initial regular Sales approval. Only the case
reseller, assigned Supervisor, and Owner can use its private conversation.
Inventory receives approved goods, prepares cleared replacements, and records
physical release; Supervisor/Owner records delivery. Existing cases and refund-
only requests retain their earlier role rules. Sources:
`app/repositories/replacement_workflow.py`, `app/repositories/replacement_messages.py`.
