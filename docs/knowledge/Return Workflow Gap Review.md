---
tags: [meattrack, returns, refunds, testing, gaps]
verified: 2026-11-03
---
# Return workflow gap review

## Scope and results

- User requested five further outcomes after the same-product replacement simulation.
- Live Chrome exercised a separate loopback application backed by a disposable synthetic database. Existing working orders/catalog/stock were not used for these five cases. No real funds or goods moved.
- Fixtures staged fulfilled purchase allocations, initial requests and the receipt/acceptance/payment-submission prerequisites of the two top-up cases. This is **not** five complete UI-only submission tests.
- Supervisor offers, cheaper-offer rejection, equal-offer acceptance, payment verification, conversion, Inventory receipt/preparation/release and delivery were exercised in the browser.
- Browser file-upload restrictions prevented automated payout-proof upload. Final synthetic transfer completion, exact-amount rejection and idempotent retry were exercised through the existing repository API, then completion was verified in staff/customer screens. A native refund-claim confirmation required user assistance. These are tool/harness limitations, not evidence of application financial defects.

| Outcome | Input | Observed result |
| --- | --- | --- |
| Equal-value alternative | Approved return credit 200; different product total 200 | Fresh acceptance required; Inventory received/prepared/released; Supervisor delivered; completed with no top-up/refund |
| Higher-value alternative | Credit 200; replacement 300; synthetic top-up 100 | Staff verified current payment; prepared/released/delivered; completed |
| Cheaper alternative | Credit 200; proposed product total 150 | Server rejected offer; stored revision/selection unchanged; inputs retained; no refund/release generated |
| Refund without top-up | Approved credit 200; conversion before receipt | Conversion recorded, settlement waited for complete receipt; one refund for 200; completed after synthetic transfer recording |
| Refund after top-up | Credit 200 + verified top-up 100; stock prepared but unreleased | Unresolved-payment attempt blocked first; verified conversion created one refund for 300, cleared preparation/holds and removed release controls; completed without delivery |

- Supplemental final checks: four cases completed; cheaper-offer case remained submitted. Two replacement releases, zero active replacement reservations, two completed refunds totaling 500 and no reconciliation flags.
- Combined order accounting: original collections 1,000 + top-ups 200 - completed refunds 500 = **700 net collections**. Original-credit refund components total 400, so **600 retained original sales**. Returning the top-up did not reduce original sales twice.
- These observations supplement the earlier regression; the full suite was not rerun for this documentation-only review.

## Open findings — not implemented

Priorities express recommended attention, not approved policy changes. Existing amount, assignment, custody and idempotency guards passed the exercised checks.

### G1 — P1: payout beneficiary is not a structured verified record

- Observed: the transfer form requires method, transaction reference, timestamp, exact amount, proof and remarks. It can record completion without a beneficiary name/account or a case-bound verification of the destination. The repository completion interface likewise accepts no destination record.
- Business consequence: an exact transfer and valid proof alone do not prove that the intended reseller received it. Conversation may contain details, but there is no enforced structured link between agreed destination and recorded payout.
- Recommendation: capture a reseller-confirmed destination with a version/audit trail; show a masked destination, case ID, components and amount before recording completion. A destination change should require renewed review. Define exceptions for cash/manual methods explicitly.
- Tests before implementation: missing/stale/unconfirmed destination rejection, authorized changes, data privacy and retry behavior; preserve historical records without inventing beneficiary data.
- Sources: `app/templates/portals/refunds.html` transfer form; `app/repositories/refunds.py::complete_order_refund`.

### G2 — P2: revised offers are confirmed before the new money comparison is shown

- Observed: changing products opens a confirmation saying the revised selection will be calculated by the server when saved. It does not show the proposed new total/top-up before publication. A 150 offer against 200 credit reaches confirmation and is rejected only after confirmation.
- Business consequence: staff cannot check the computed financial promise in the confirmation before publishing an alternative. Reseller acceptance still protects fulfillment; this is an offer-review gap, not evidence that cheaper offers can be stored.
- Recommendation: server-computed preview for revised quantities/products, showing proposed lines, credit and exact top-up; bind publication to the reviewed revision/preview and revalidate under locks.
- Tests first: changed quantities, mixed original/catalog prices, lower-value rejection, stale preview and confirmation totals.
- Sources: `app/static/js/return_details.js` changed-offer confirmation branch; `app/templates/portals/returns/_case_review.html`; `app/repositories/replacement_workflow.py::review`.

### G3 — P2: receipt backlog includes requests Inventory is not yet authorized to receive

- Observed: after the four valid cases finished, Inventory's summary still showed two packs awaiting receipt solely from the unapproved cheaper-offer case. That case remained submitted.
- Source explains it: `inventory_return_totals` sums outstanding packs for every non-rejected request, including submitted cases; receipt action guards correctly require authorization.
- Business consequence: the headline can overstate executable warehouse work and disagree with the actionable receipt queue.
- Recommendation: count only receipt-eligible cases as awaiting receipt, or split proposed packs from approved expected arrivals with clear labels. Retain visibility of history separately.
- Tests first: submitted, approved, mismatch, rejected and received cases; summary and actionable queue must use intentional matching predicates.
- Sources: `app/repositories/product_returns.py::inventory_return_totals`; `app/repositories/portal_dashboard.py`; Inventory return template.

### G4 — P2: rejected offer leaves a contradictory saving status

- Observed: the cheaper offer showed the correct rejection and preserved editable inputs, but the persistent action bar continued to say “Saving return action…”.
- Business consequence: staff can mistake a finished failure for an in-progress save, retry unnecessarily or be unsure which offer is recorded. Idempotency and transactional rejection still held.
- Recommendation: reset the shared action-bar status in the error path; distinguish failed, unknown/retryable and successful outcomes consistently with the inline message.
- Tests first: server rejection and network timeout; assert both inline and persistent status plus restored controls, unchanged record and retained operation key.
- Source: `app/static/js/return_details.js::bindConfirmation` / `postAction` catch path. Evidence: `outputs/live-simulation/five-lower-blocked.png`.

### G5 — P2: refund payout workspace hides the refund's component split

- Observed: Return details correctly showed credit 200 + verified top-up 100 = refund 300. The separate payout workspace displayed locked refund 300 with the generic “Verified return credit / replacement balance” label and order-wide reconciliation, without the case's 200/100 split.
- Business consequence: the processor must navigate elsewhere to distinguish returned original proceeds from a reversed top-up; order-wide top-ups can include other cases.
- Recommendation: show the selected refund's immutable original-credit/top-up breakdown in its header and transfer confirmation, while retaining separately labeled order-wide totals.
- Tests first: original-only refund, top-up conversion, multiple cases on one order, historical refunds and Inventory financial privacy.
- Sources: `app/templates/portals/refunds.html` refund details/reconciliation; `app/templates/portals/returns/_case_financial_card.html`; `app/repositories/return_policy.py::refund_components`.

### Q1 — policy question (P2 attention): how does an unanswered/unacceptable offer end?

- Observed: the reseller sees acceptance and conversation actions; no explicit alternative-decline decision or response deadline was shown. Current revision/acceptance metadata do not define an offer-response SLA. Requests do not gain stock priority merely by waiting.
- This review does not prove every manual escalation practice is absent. It identifies a product/operating-policy decision that needs an owner, not a demonstrated accounting bug.
- Decide: how a reseller declines a changed offer, response/restock targets, reminders, escalation owner and how staff agree on another offer or authorized refund. Do not automatically convert, forfeit credit, cancel paid obligations or release funds just because a timer expires.
- Tests after the decision: decline with explanation, reminders, reassignment, verified-payment preservation and final authorized resolution.
- Sources: `app/templates/portals/returns/_offer_comparison.html`; `app/repositories/replacement_workflow.py`; policy/revision columns in `database/schema.sql`.

## Evidence and follow-up

- Ignored local evidence: `outputs/live-simulation/five-*.png`, `five-backend-checks.json` and `FIVE-OUTCOMES.md`. Screenshots use synthetic data and were visually inspected.
- Do not treat fixture mistakes (initially short synthetic payment references), cookie collisions between local ports, native-dialog control timeouts or extension upload permission failures as business defects. Corrected fixture inputs were re-verified before recording outcomes.
- Recommended order: G1 payout destination, G2 money preview, G3 queue predicates, G4 failure status, G5 component visibility; resolve the policy question with the business owner. Add failing tests for each accepted change before implementation.
- Disposable server/database were removed after evidence capture. A read-only working-data check confirmed the previous completed order/return and unchanged finished physical stock endpoint.
- No application/schema behavior was changed or production deployment performed in this review.

Related: [[Replacement Workflow]], [[Returns and Financials]], [[Orders and Inventory]], [[Roles and Access]], [[Testing and Change Impact]], [[Questions and Contradictions]].
