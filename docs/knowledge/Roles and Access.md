---
tags: [meattrack, permissions]
verified: 2026-11-03
---
# Roles and access
Portal navigation describes available screens; server-side capabilities and
ownership-scoped queries enforce access. A hidden button is not an authorization
boundary. Sales supervisor is a portal role backed by the team-leader account
type and a specialized team-leader role.

| Actor | Main responsibilities | Connections |
| --- | --- | --- |
| Public visitor | Catalog and FAQ chatbot; direct phone/email contact for a person, and Business Owner email for reseller opportunities | [[Background Work and Integrations]] |
| Reseller | Own cart/orders, payment proof, own return cases | [[Orders and Inventory]], [[Returns and Financials]] |
| Sales team leader | Assigned reseller work, order/payment review, initial return acceptance/rejection and assigned-case tracking | Ownership affects evidence, messaging, reports |
| Sales supervisor | Supervisor-scoped orders, reseller-to-sales-leader assignments, accepted return processing, refunds | Cannot create portal accounts; refund permission differs from sales visibility |
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
Login email OTP is on by default in every environment. Owner-issued reseller
registration invitations are bound to one email, expire after seven days, and can
create one pending account. The account becomes active only after the invitee
verifies that email with the existing OTP mechanism. Reseller role and status are
assigned by the server. Staff activation, password-change, and recovery flows remain
separate. Checked against `app/capabilities.py`, `app/portals/owner.py`,
`app/routers/reseller_registration.py`, `app/repositories/reseller_invitations.py`,
`app/repositories/accounts.py`,
`app/portals/refunds.py`, and `app/repositories/product_returns.py` on 2026-11-03.
See [[Returns and Financials]] and [[Decisions]].

New reseller registration and staff account activation collect separate
acknowledgements for the Privacy Notice and Terms. Existing accounts without
the current versions are prompted after password and any required login OTP
verification, before a portal session is established. Routine login only offers
Remember me and links to the policy pages. A new `account_policy_acceptances`
ledger stores each policy type, version, source, and timestamp independently;
this acknowledgement ledger is separate from `user_consents`. Policy versions
default to `CONSENT_VERSION` and can be configured independently with
`PRIVACY_NOTICE_VERSION` and `TERMS_VERSION`. Sources: `app/config.py`,
`app/routers/auth.py`, `app/routers/reseller_registration.py`,
`app/repositories/accounts.py`, `app/repositories/reseller_invitations.py`,
`app/templates/login.html`, `app/templates/activate.html`,
`app/templates/reseller_register.html`, and migration
`database/migrations/052_account_policy_acceptances.sql`. See [[Decisions]] and
[[Architecture]].

Sessions are backed by PostgreSQL. Portal sessions expire after 15 minutes without
a user action; page navigation and a heartbeat after recent keyboard, pointer,
scroll, or touch activity refresh the deadline. Background message polling alone
does not. This works while the browser is open or closed. The login form's
optional Remember me choice keeps the browser cookie across restarts for up to
30 days, while the server-side inactivity deadline remains 15 minutes. Without
it, the cookie ends when the browser closes. Session rotation, CSRF enforcement,
and rate controls connect authorization to [[Architecture]]. Check configuration
and implementation when changing defaults; see the documented drift in
[[Questions and Contradictions]].

### Login OTP on recognized devices

After successful login OTP verification, the browser receives a random, HttpOnly
device cookie and PostgreSQL stores only its SHA-256 hash for that account. The
device is trusted for a fixed 30 days from verification; signing in during that
period does not extend the window. A first sign-in, a different browser/device,
or an expired trust record requires email OTP again. This applies only to login;
registration, password changes, and recovery keep their own OTP rules. Sources:
`app/routers/auth.py`, `app/repositories/accounts.py`, and migration
`database/migrations/051_trusted_login_devices.sql`.

Opening `/login` with a still-valid portal session redirects to that account's
default portal page, so a persistent session does not appear signed out simply
because the browser reopened the login URL. Source: `app/routers/auth.py`.

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

Inventory's Stock alerts destination uses `inventory.view` and is listed only in
Inventory navigation. Sales, Supervisor, and Reseller roles do not gain this page.
It reads shared stock alert episodes without another Owner's personal review state;
review, closure, and policy-edit endpoints remain Owner-only. Checked 2026-11-01
against `app/capabilities.py`, `app/portal_config.py`, and
`tests/test_inventory_alert_workspace.py`. See [[Owner Portal]] and
[[Orders and Inventory]].


Inventory tools (2026-11-01) is an Inventory navigation destination guarded by
`inventory.view`; writes additionally require `inventory.manage` and an active
Inventory actor. Stock write repositories also validate the actor. Sales,
Supervisor, and Reseller do not gain adjustment, package-dispatch, batch-removal,
or photo-upload permissions. Product images are public catalog media; private
return/payment media access is unchanged. Sources: `app/capabilities.py`,
`app/portals/inventory_tools.py`, `app/repositories/inventory/tools.py`.
See [[Orders and Inventory]].

## Login form CSRF recovery (2026-11-02)

Password, OTP, and Use another account forms render CSRF hidden fields on the
server; they do not depend on client-side injection. Login responses use
`Cache-Control: no-store`. Invalid/missing login tokens still reject authentication
with HTTP 403, but browser requests receive a fresh sign-in form and retry message.
Explicit JSON requests retain the machine-readable 403. Successful authentication
still rotates the session. Sources: `app/templates/login.html`,
`app/routers/auth.py`, `app/application.py`, `app/security_controls.py`,
`tests/test_login_csrf.py`. The live browser showed a token on the fresh login form;
38 login/account-action checks passed. See [[Testing and Change Impact]].
