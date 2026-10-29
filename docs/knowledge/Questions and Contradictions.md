---
tags: [meattrack, questions, documentation]
verified: 2026-10-28
---
# Questions and contradictions

These are documentation discrepancies observed while creating this vault. The
setup does not change application policy or rewrite older guides.

| Observation | Current evidence | How to handle it |
| --- | --- | --- |
| Earlier README deployment heading named Hostinger | Project owner confirmed Contabo VPS on 2026-10-29; README corrected | Use Contabo for hosting references; expiry remains unverified. See [[Database and Operations]] |
| `docs/product-lifecycle.md` says return photos are optional | `docs/refund-return-process.md` and `app/repositories/product_returns.py` require evidence for new requests | Use current required-evidence behavior; preserve old cases |
| Earlier guides and migration 036 describe a separate Sales confirmation after Inventory verification | The 2026-10-29 user-directed complete-match receipt now clears the existing Sales approval atomically; historical reconciliation can still use manual confirmation | Require current Sales approval, complete verified custody, accepted selection, and payment clearance; see [[Returns and Financials]] and [[Decisions]] |
| `docs/HANDOFF.md` lists 33 migrations and historical Git/deployment status | Local source has migrations through 036 | Read actual migration records and verify server revision for deployment work |
| README quick start enables local security controls, while its closing security note says local controls are off by default | Actual behavior depends on `app/config.py`, Compose values, and environment | Inspect relevant runtime settings before explaining defaults |

Related: [[Returns and Financials]], [[Roles and Access]], [[Database and Operations]],
[[Testing and Change Impact]]. Treat contradictions as questions to resolve in
the relevant task; do not silently reinterpret current code from an older note.

Sources: [lifecycle guide](../product-lifecycle.md), [current process](../refund-return-process.md),
[return implementation](../../app/repositories/product_returns.py),
[historical handoff](../HANDOFF.md), [README](../../README.md),
[configuration](../../app/config.py), [local Compose](../../compose.yml).
