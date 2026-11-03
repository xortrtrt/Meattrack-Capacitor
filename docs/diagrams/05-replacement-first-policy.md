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
- Alternatives require equal/higher value and fresh acceptance. Higher value requires verified extra payment before preparation.
- Inventory receipt and acceptance can progress separately; both must be complete before a top-up/preparation can proceed.
- Conversion is an approved pre-release exception; settlement still requires complete receipt. Verified top-up is refunded separately from the original-sale component.
- Orders/replacements share locking and availability. Replacement shortage waits; the existing paid-order shortage rejection/payment-review refund path remains intact.
- Returns enter non-sellable custody. Preparation reserves; warehouse release deducts physical stock.

Sources: `app/repositories/return_policy.py`, `product_returns.py`, `replacement_workflow.py`,
`refund_integrity.py`, `refunds.py`, `inventory/allocation.py`, `orders.py`, migrations 048–049,
`tests/test_replacement_first_policy.py`, `tests/test_replacement_first_stock.py`.

Related: [[Replacement Workflow]], [[Returns and Financials]], [[Orders and Inventory]],
[[Decisions]], [change plan](../replacement-first-change-plan.md).
