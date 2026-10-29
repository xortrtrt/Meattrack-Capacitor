---
tags: [meattrack, permissions]
verified: 2026-10-28
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
| Sales team leader | Assigned reseller work, order/payment review, return review and confirmation | Ownership affects evidence, messaging, reports |
| Sales supervisor | Supervisor-scoped onboarding, orders, refund processing | Refund permission differs from sales visibility |
| Inventory team leader | Production, stock, expiry, physical return receipt/verification, replacement release | Physical quantities and custody |
| Owner | Broad oversight, accounts, pricing, operations, recovery and settings | Sensitive operations may require step-up verification |

Return custody and financial processing are separate responsibilities. Inventory
views focus on stock and quantities; Sales approves the resolution and reviews
reported mismatches. Confirming a complete matching receipt clears that existing
approval in one Inventory action. Authorized Supervisor/Owner processors handle transfers. Sales
leaders can inspect assigned refund information without inheriting processing
permissions. Direct evidence-download endpoints must preserve the same scope.

Sales team leaders review product returns and follow money refunds from the shared
**Refunds & returns** entry. Combining the pages does not grant Sales transfer
processing or Inventory financial access. Legacy Sales return URLs and notifications
resolve to `/portal/team-leader/refunds?view=returns`. Sources checked 2026-10-29:
`app/portal_config.py`, `app/portals/shared.py`, `app/portals/refunds.py`, and
`app/portals/product_returns.py`. See [[Returns and Financials]].

Sessions are backed by PostgreSQL. Login/activation/password-change handling,
session rotation, CSRF enforcement, and rate controls connect authorization to
[[Architecture]]. Check configuration and implementation when changing defaults;
see the documented drift in [[Questions and Contradictions]].

Sources: [capabilities](../../app/capabilities.py),
[portal dependencies](../../app/portal_dependencies.py), [navigation](../../app/portal_config.py),
[authentication](../../app/routers/auth.py), [sessions](../../app/web_sessions.py),
[return process](../refund-return-process.md), [owner actions](../../app/portals/owner.py).
