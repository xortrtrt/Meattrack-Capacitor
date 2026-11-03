# Replacement-first returns and shared inventory

Verified local implementation addendum, 2026-11-03. All 1,027 regression cases and
eight post-maintenance smoke checks passed; migrations through 049 and the local
transaction-only reset completed with preservation checks. No production deployment.
These diagrams cover new `return_policy_version=2` cases; historical policies retain their recorded behavior.

| Diagram | View | Editable source |
| --- | --- | --- |
| Replacement-first return | [SVG](rendered/replacement-first-returns.svg) | [Mermaid](mermaid/replacement-first-returns.mmd) |
| Authorized refund conversion | [SVG](rendered/refund-conversion.svg) | [Mermaid](mermaid/refund-conversion.mmd) |
| Order/replacement stock competition | [SVG](rendered/shared-order-replacement-stock.svg) | [Mermaid](mermaid/shared-order-replacement-stock.mmd) |

- Credit means approved returned quantities at original paid prices, not the full order value.
- Alternatives require equal value and fresh acceptance; replacement transactions do not collect or refund price differences.
- Supervisor approval locks and reserves eligible FEFO stock. If the approved quantity is insufficient, the case moves to the refund process and notifies the reseller.
- A shortage refund still requires complete receipt and verification of the approved returned goods, including expired goods in Expired holding. Refund settlement follows the existing workflow.
- The existing paid-order shortage rejection/payment-review refund path remains intact. Returns enter non-sellable custody; warehouse release deducts physical replacement stock.
- Returns enter non-sellable custody. Inventory preparation revalidates approval-time reservations; warehouse release deducts physical stock.

Sources: `app/repositories/return_policy.py`, `product_returns.py`, `replacement_workflow.py`,
`refund_integrity.py`, `refunds.py`, `inventory/allocation.py`, `orders.py`, migrations 048–049,
`tests/test_replacement_first_policy.py`, `tests/test_replacement_first_stock.py`.

Related: [[Replacement Workflow]], [[Returns and Financials]], [[Orders and Inventory]],
[[Decisions]], [change plan](../replacement-first-change-plan.md).
