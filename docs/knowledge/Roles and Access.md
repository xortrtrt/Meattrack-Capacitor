---
tags: [meattrack, permissions]
verified: 2026-11-03
---
# Roles and access

## Assigned reseller directory (2026-11-04)

Sales Leaders and Sales Supervisors both have **My Resellers**, at
`/portal/team-leader/resellers` and `/portal/sales-supervisor/resellers` respectively.
Both reuse the same searchable, filtered, paginated read-only roster, scoped by
the signed-in account's `resellers.team_leader_account_id`. Message links retain
the current portal role. The Supervisor **Accounts** page remains the separate
assignment-management workspace described in [[Owner Portal]]. Checked against
`app/portal_config.py`, `app/capabilities.py`, `app/portals/team_leader.py`,
`app/repositories/accounts.py`, and
`app/templates/portals/team-leader/resellers.html` on 2026-11-04.

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
| Owner | Business oversight, accounts, pricing, financial decisions and settings; read-only physical stock oversight | Actions require authenticated capabilities and audit records |

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

In People & access, the Owner can deactivate and reactivate accounts with a
required reason. Both status changes attempt an immediate email to the affected
account containing that reason. Deactivation also revokes its current sessions. Failed email
delivery remains queued for retry, and the Owner sees whether the provider
accepted the message. Reactivation restores account access. Password reset is
managed from the user's own portal profile, so the Owner directory no longer
presents a reset-email action. The status change, reason, audit event, and
deactivation email queue entry are committed together.
Sources: `app/templates/portals/owner/users_access.html`,
`app/portals/owner.py`, `app/repositories/superuser.py`, `app/worker.py`, and
`app/emailer.py`. Checked 2026-11-03. See [[Owner Portal]] and
[[Background Work and Integrations]].

New reseller registration and staff account activation collect separate
acknowledgements for the Privacy Notice and Terms. Accounts that completed
signup proceed directly to their portal after password and any required login
OTP verification; routine login does not prompt them to accept policy versions
again. A new `account_policy_acceptances` ledger stores each policy type,
version, source, and timestamp independently; this acknowledgement ledger is
separate from `user_consents`. Policy versions default to `CONSENT_VERSION` and
can be configured independently with `PRIVACY_NOTICE_VERSION` and
`TERMS_VERSION`. Sources: `app/config.py`, `app/routers/auth.py`,
`app/routers/reseller_registration.py`, `app/repositories/accounts.py`,
`app/repositories/reseller_invitations.py`, `app/templates/activate.html`,
`app/templates/reseller_register.html`, and migration
`database/migrations/052_account_policy_acceptances.sql`. Checked 2026-11-03.
See [[Decisions]] and [[Architecture]].

Invited reseller registration requires a password with at least eight
characters, uppercase and lowercase letters, a number, and a special character.
The contact number is stored in `+63` format from a 10-digit Philippine mobile
number beginning with 9; the business address is required and limited to 5–500
characters. The browser validates these fields for convenience, while the
registration repository enforces them before creating the pending account.
Production CSRF origin checks accept the request host and the configured public
`APP_BASE_URL` host, allowing the reverse-proxy deployment to validate the
browser's public origin. Sources: `app/templates/reseller_register.html`,
`app/repositories/reseller_invitations.py`, `app/security_controls.py`, and
`app/config.py`. Checked 2026-11-03. See [[Architecture]] and [[Decisions]].

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

The pending login OTP page offers **Resend OTP**. Login codes can be resent once
every three minutes. Local Compose allows up to 50 OTP requests per account per
hour; other environments default to five unless configured. The server enforces
both limits even if the page is submitted early. Registration OTP retains its
separate 60-second cooldown. Sources: `app/routers/auth.py`,
`app/repositories/accounts.py`, `app/security_controls.py`, `app/config.py`,
`compose.yml`, and `app/templates/login.html`.

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


Inventory tools was removed on 2026-11-04, including its manual raw-material
adjustment/removal, package component setup/dispatch, and batch archival actions.
Its navigation, page loader, and write endpoints are gone; historical stock and
audit records remain. Product photo updates use the Inventory Product Details
route, and public catalog photo delivery remains available. Sources:
`app/portal_config.py`, `app/capabilities.py`, `app/portals/team_leader.py`,
`app/portals/product_media.py`, and `app/repositories/inventory/products.py`.
See [[Orders and Inventory]] and [[Decisions]].

## Remove a profile picture (2026-11-04)

Profile pictures use the shared `app/templates/portals/_profile_picture.html`
across portal roles. When a saved image exists, **Remove profile picture** is
available beside the picture and inside the camera editor. Both submit the
same authenticated removal action in `app/portals/shared.py`, clear the saved
image and session crop, and return to the profile with a success message. The
editor removal button submits the separate removal form so choosing an upload
is unnecessary. Sources checked 2026-11-04: those files and
`app/repositories/accounts.py`. See [[Architecture]].

## Verify email before choosing a new password (2026-11-03)

Owner, Reseller, Sales Leader, Sales Supervisor and Inventory profiles use one
shared password workflow. Confirm the current password, send a six-digit email
OTP, verify it, then choose and confirm the new password. No new-password fields
are rendered before verification, and the save endpoint requires the same
session's account-bound, single-use grant. Delivery failure cancels the challenge
and displays an error rather than unlocking password entry.

Challenges expire after ten minutes, allow five incorrect attempts, and can be
requested once per 60 seconds subject to `OTP_REQUESTS_PER_HOUR`. Verification
leaves at most five minutes to save. A replacement request invalidates earlier
challenges; cancellation, expiry, changed credentials, and reuse prevent saving.
Saving enforces the existing strong-password policy, revokes all portal sessions
and recognized-device tokens, and sends every role to sign-in. The database
stores hashed OTPs and grants in `account_password_change_challenges` (migration
053). Older pending-password OTP records cannot be completed through portal
routes. Sources: `app/portals/password_change.py`,
`app/repositories/password_changes.py`, `app/templates/portals/_password_change.html`,
`app/portals/shared.py`, `app/emailer.py`, and `app/worker.py`.
See [[Architecture]], [[Decisions]], and [[Background Work and Integrations]].

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

## Owner stock oversight is read-only (2026-11-04)

The Owner's wildcard capability explicitly excludes `inventory.manage`,
`inventory.override`, and `returns.receive`. Stock pages and direct HTTP actions
therefore cannot record production, material receipts, corrections, pull-outs,
disposal or physical return receipt/preparation/release. Alert review/closure
routes also use the excluded inventory override capability. Stock-writer
repositories reject Owner actors, while automated stock changes resulting from
permitted order/financial decisions retain their business rules. Inventory team
members retain stock management and physical custody permissions. Product prices
remain editable through `inventory.prices`; financial returns/refunds and other
Owner capabilities remain separate. Sources: `app/capabilities.py`,
`app/portals/owner.py`, `app/portals/product_returns.py`,
`app/repositories/inventory/movements.py`, `app/repositories/product_returns.py`,
`app/repositories/superuser.py`, and `app/portal_config.py`.
See [[Owner Portal]], [[Orders and Inventory]], and [[Decisions]].

## Registration request origins (2026-11-04)

Registration responses use `Referrer-Policy: strict-origin` so invitation paths
and query tokens are omitted from referrer headers while HTTPS form submissions
retain a usable origin. `no-referrer` can produce `Origin: null` on browser POSTs,
which production correctly rejects before registration runs. Keep the production
origin and CSRF token checks enabled. After deploying the correction, reopen the
invitation page to receive the new response policy before submitting again.
Sources checked: `app/routers/reseller_registration.py`, `app/security_controls.py`.
See [[Database and Operations]] and [[Decisions]]. This is a source fix; deployment
has not been performed.
