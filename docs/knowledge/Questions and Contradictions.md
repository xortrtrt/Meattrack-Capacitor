---
tags: [meattrack, questions, documentation]
verified: 2026-11-03
---
# Questions and contradictions

These are documentation discrepancies observed while creating this vault. The
setup does not change application policy or rewrite older guides.

| Observation | Current evidence | How to handle it |
| --- | --- | --- |
| `database/meattrack_erd.mmd` is an older partial ERD, including an overly restrictive reseller/account link | Current baseline has 71 tables; `accounts.reseller_id` is nullable and non-unique; the migration runner adds one metadata table | Use the current Mermaid pack in [[System Diagrams]] and verify final CREATE/ALTER/index definitions for schema work |
| Earlier README deployment heading named Hostinger | Project owner confirmed Contabo VPS on 2026-10-29; README corrected | Use Contabo for hosting references; expiry remains unverified. See [[Database and Operations]] |
| `docs/product-lifecycle.md` says return photos are optional | `docs/refund-return-process.md` and `app/repositories/product_returns.py` require evidence for new requests | Use current required-evidence behavior; preserve old cases |
| Older notes describe unpaid stock holds and a combined Sales money-refund workspace | User-directed migrations 040–042 introduce paid-only reservations, processor handoff, and internal media/read cursors | Follow [[Orders and Inventory]], [[Returns and Financials]], and [[Roles and Access]]; verify deployment separately |
| Earlier guides and migration 036 describe a separate Sales confirmation after Inventory verification | The 2026-10-29 user-directed complete-match receipt now clears the existing Sales approval atomically; historical reconciliation can still use manual confirmation | Require current Sales approval, complete verified custody, accepted selection, and payment clearance; see [[Returns and Financials]] and [[Decisions]] |
| `docs/HANDOFF.md` lists 33 migrations and historical Git/deployment status | Local source and the local development database now include migration 049 | Read actual migration records and verify server revision for deployment work |
| Older lifecycle/refund guides and the original diagram pack describe customer-selected refunds or cheaper replacements | New public requests use separate policy version 2: default original products/prices, equal/higher processor offers and an authorized pre-release refund exception | Keep those older descriptions for historical policy-1 cases; use [[Replacement Workflow]] and `docs/diagrams/05-replacement-first-policy.md` for the new policy |
| README quick start enables local security controls, while its closing security note says local controls are off by default | Actual behavior depends on `app/config.py`, Compose values, and environment | Inspect relevant runtime settings before explaining defaults |

Related: [[Returns and Financials]], [[Roles and Access]], [[Database and Operations]],
[[Testing and Change Impact]]. Treat contradictions as questions to resolve in
the relevant task; do not silently reinterpret current code from an older note.

Sources: [lifecycle guide](../product-lifecycle.md), [current process](../refund-return-process.md),
[return implementation](../../app/repositories/product_returns.py),
[historical handoff](../HANDOFF.md), [README](../../README.md),
[configuration](../../app/config.py), [local Compose](../../compose.yml).


## Return operating gaps found in live review (2026-11-03)

[[Return Workflow Gap Review]] separates five observed control/UX gaps from an
unresolved offer-decline/response-time policy. Beneficiary verification, staff
comparison previews, receipt-backlog scope, failure status and payout component
display remain open. Passing financial tests do not establish these controls exist.
