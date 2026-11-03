# Replacement-first returns: implementation and verification plan

Status: implemented and verified locally, 2026-11-03. The execution plan below is
retained with its completion record. No production deployment is included.

## Rules to apply

| Module | Required behavior |
| --- | --- |
| Return request | Default to the approved returned products and quantities at their original paid prices. Preserve the 120-hour window, evidence and unclaimed-quantity checks. |
| Replacement comparison | Compare against the approved **returned-item credit**, not the full original order. Catalog price changes do not reprice matching original quantities. |
| Alternatives | Assigned Supervisor/Owner records why originals cannot be supplied and offers an equal/higher alternative. Reseller accepts each changed offer. Lower-value offers are rejected. |
| Extra payment | Equal value needs no payment. Higher value requires the exact difference, reviewed payment details and confirmed receipt of funds before preparation. |
| Refund exception | Preserve paid rejected-order refunds. For an approved replacement that cannot be supplied, Supervisor/Owner may convert before warehouse release. Refund approved credit plus verified top-up. |
| Physical returns | Inventory verifies complete receipt into non-sellable custody. A return is a goods process; a refund is a money process. Returned goods do not become sellable stock. |
| Shared inventory | Orders and replacements use the same FEFO allocator. First successful committed reservation wins; submission time creates no priority. |
| Existing cases | Preserve historical refund and cheaper-replacement behavior under their recorded policy. |

## Current source and evidence

- Graphify indexed `2370bfc`; current local code/schema/tests are authoritative for this change.
- Reused sequence: Supervisor review → complete Inventory receipt → payment clearance → FEFO preparation → warehouse release → Supervisor delivery.
- Tests were changed first: 34 intended policy failures, then reproduced UI recovery/history gaps before their fixes. Explicit legacy fixtures preserve historical behavior.
- Final full suite: 1,027 passed, zero failures/errors/skips; exact collected-case coverage and unchanged 404-file source checksum verified across three isolated databases/apps, all cleaned afterward.
- Ruff, configured mypy (83 sources), five JavaScript syntax checks, 122-template compilation and whitespace checks passed. Browser screenshots were inspected at phone/desktop sizes. Premium audit added no findings; 341 existing findings remain, with zero unresolved ownership.
- Private backup restores, reset rehearsals, migrations through 049 and the separately authorized local transaction reset completed. All 37 transaction tables empty; 160 foreign keys valid; protected physical/custody/accounts/catalog/messages/identifiers preserved.
- Eight post-maintenance synthetic checks passed; disposable schemas removed, public state unchanged and five-role transaction queues empty. App/worker running; health HTTP 200.
- Review evidence: `outputs/replacement-policy/VERIFICATION.md`, `REVIEW.html`, `full-coverage-result.json`, `local-maintenance-result.json`, `post-maintenance-smoke-result.json`.

## 1. Complete the test changes before further implementation

Completed before implementation: policy tests and affected expectations were updated and run red, with historical fixtures explicitly preserved. Clients cannot select legacy policy.

| Test module | Required assertions |
| --- | --- |
| `tests/test_replacement_first_policy.py` | Partial-order credit; multiple original prices; changed catalog price; inactive originals; tampered selection/token; duplicate retry; equal/higher/lower offers; acceptance invalidation; mismatch corrections. |
| Same policy suite | Exact top-up and provider confirmation; conversion before receipt and after top-up; no settlement before complete receipt; no conversion after release; unauthorized/stale conversion; unresolved proof; idempotency-key conflict; verified collections cannot be declared unreceived. |
| `tests/test_replacement_first_stock.py` | Order wins, replacement wins, two replacements race, insufficient stock, rollback, expiry/release race and eventual restock. Paid-order shortage retains its existing rejection/payment-review refund behavior. |
| Financial/report suites | Separate original-credit/top-up collection ceilings; one refund obligation; proof/reference uniqueness; retries; net collections; no second reduction of original sales; immutable submitted snapshots. |
| `tests/test_replacement_first_portal.py` and existing browser suites | Reseller/Supervisor/Owner/Inventory/Sales permissions; stale forms; retry with fields/files/key retained; safe confirmation focus; unsaved changes; keyboard; post-conversion queues/actions; Inventory financial privacy. |
| `tests/test_replacement_first_migration_reset.py` | Baseline and migration chain; unchanged historical cases; transaction reset preserves physical stock/custody/accounts/catalog/unrelated messages; unknown dependencies and injected failures roll back; private backup/restore verification. |

Run new or revised behavior tests against the current working tree. Record whether each fails because behavior is missing, passes because the partial implementation already supplies it, or exposes a pre-existing defect. Do not manufacture a red result by reverting user work. For remaining defects, capture a failing test before fixing them.

Gate: complete affected-test matrix, runnable PostgreSQL/Chromium prerequisites, explained baseline failures and explicit coverage for every rule above. A skipped database test does not satisfy this gate.

## 2. Finish implementation by module

| Sequence | Module and work | Completion gate |
| --- | --- | --- |
| 1 | Policy/schema: separate `return_policy_version`; retain workflow version 2; additive migration and matching baseline; new policy assigned server-side. | Fresh schema and full migration chain pass; existing rows retain their policy/history. |
| 2 | Request/comparison: derive defaults server-side; enforce credit floor at publication and mutation boundaries; retain signed comparison, eligibility and claim limits. | Request/pricing/tampering/idempotency tests pass. |
| 3 | Review/payment: preserve assignment, revision acceptance, mismatch and receipt gates; verify top-up before preparation; block revising paid/released offers. | Equal/higher alternatives and all stale/unauthorized paths pass. |
| 4 | Refund conversion: lock current case/financial state; require public explanation, internal reason, UUID and reviewed fingerprint; resolve unverified funds; clear preparation/reservations atomically. | Payment-versus-conversion and release-versus-conversion races produce one valid outcome. No further replacement action succeeds. |
| 5 | Refund/reporting: one component-aware obligation; approved credit plus verified top-up; original-sale reduction excludes returned top-up; retain existing refund attempt/evidence controls. | Refund totals, collection ceilings, reconciliation and frozen snapshots pass. |
| 6 | Inventory: retain shared deterministic FEFO locks, no replacement priority, separate prepare/release, expiry checks and retry after restock. | Both winner orders pass; no oversell, double deduction or stolen reservation. |
| 7 | Portals/projections: update request, dedicated pages, Owner/legacy dialogs, conversation links, notifications, timelines, queues and dashboards together. | Correct next actor/action in every state; converted cases do not wait for delivery. |

## 3. Check the actual frontend

- Inspect Reseller, Supervisor, Owner and Inventory screens at phone, tablet and desktop widths, including a narrow 320px viewport.
- Check form, modal, footer and confirmation bounds; long reasons, amounts and errors; horizontal overflow; scrollbars; keyboard focus; reduced motion and loading/retry states.
- Test confirmation Cancel, failed conversion retry, stale conversion, unreceived-payment resolution, Keep editing and Discard changes.
- Inspect rendered screenshots directly after the final changes. Supply those screenshots and updated diagrams with completion results.
- Run the required design audit and document genuine pre-existing findings separately from changed-surface defects. Do not bypass checks or add inert controls to satisfy a scanner.

## 4. Run the complete verification gates

1. Focused new-policy, legacy, money, stock, migration/reset and browser suites.
2. Full pytest with PostgreSQL required and Chromium available; use disposable schemas and a fresh writable private temporary directory.
3. External-server E2E against a separately seeded disposable database/application. Do not seed or run destructive E2E against the working local database.
4. Ruff, configured mypy, changed JavaScript syntax checks, all Jinja template compilation, design/token checks and `git diff --check`.
5. Review failed and skipped tests individually. Fix genuine regressions; update stale presentation expectations only while retaining the business/permission assertions.

Gate: no unexplained failures, no prerequisite skips for required acceptance cases, reconciled money/stock, and reviewed screenshots. A passing focused suite alone is insufficient.

## 5. Keep local data maintenance separate

This step comes from the previously authorized full specification. It is not part of request handling, startup or migration execution.

- After verification, stop local application/worker writes and confirm the loopback target.
- Create a **fresh** private backup outside the repository; actually restore it and rehearse the narrow transaction reset on the disposable restore. Earlier backup manifests become stale after later writes/schema changes.
- Apply only transaction-only reset with an explicit reviewed dependency list. Fail closed on unknown foreign keys; no unrestricted cascade.
- Preserve accounts, catalog, recipes/packages, settings, unrelated messages, migration history, batch identities/expiry/quality/physical quantities, non-sellable custody and advancing identifiers.
- Do not restore sold/released packs when removing transactions. Detach reviewed historical references or preserve reconciled history without changing physical balances.
- Commit only after empty transaction tables, preserved-data equality, valid foreign keys and restored trigger state. Otherwise roll back.
- Restart services; verify health and clean read-only queues. Smoke-test fresh order/replacement flows using disposable synthetic state.
- No deployed-data reset or deployment is included.

## Verified flow diagrams

Use the [replacement-first policy addendum](diagrams/05-replacement-first-policy.md)
for the three checked flows: replacement requests, authorized refund conversion,
and shared order/replacement stock. Each has editable Mermaid plus SVG/PNG renders.
Receipt and acceptance may progress separately; both gate payment/preparation.

## Completion record

Update [[Replacement Workflow]], [[Returns and Financials]], [[Database and Operations]], [[Testing and Change Impact]], [[Questions and Contradictions]] and [[Decisions]] with verified source links and rationale. Refresh the relevant module diagrams. Record actual checks, limitations and local maintenance outcome; do not claim deployment or zero possible future errors.

Completed locally: test-first implementation, full regression, static/template/design checks,
rendered-screen/diagram inspection, verified backups and reset rehearsals, migrations
through 049, transaction-only reset, preservation/FK/identifier checks, service restart,
eight synthetic smoke checks and read-only queues. Relevant knowledge notes and
decision rationale are updated. No deployed changes were made. Existing UI audit
debt remains documented; verification cannot guarantee every possible future outcome.
