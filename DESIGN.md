---
version: alpha
name: MEATTRACK portals
description: Warm, compact operations workspaces for purchasing, Sales and warehouse teams.
colors:
  primary: "#0d0c0b"
  gold: "#d7b621"
  gold-deep: "#aa8616"
  background: "#fbf7ec"
  surface: "#ffffff"
  text: "#241f19"
  muted: "#6f675d"
  border: "#d7c99a"
  success: "#2f6f4e"
  danger: "#b23b35"
  warning: "#9a6a00"
typography:
  heading:
    fontFamily: "Rubik, Segoe UI, Arial, sans-serif"
    fontSize: "32px"
    lineHeight: "1.3"
  body:
    fontFamily: "Rubik, Segoe UI, Arial, sans-serif"
    fontSize: "14px"
    lineHeight: "1.5"
rounded:
  DEFAULT: "12px"
  control: "8px"
spacing:
  section-gap: "20px"
  panel-padding: "18px"
  page-max: "1440px"
components:
  dashboard-panel:
    rounded: "{rounded.DEFAULT}"
    backgroundColor: "{colors.surface}"
    textColor: "{colors.text}"
  dashboard-action:
    height: "44px"
    textColor: "{colors.gold-deep}"
---

# MEATTRACK portal design

## Overview

The supplied MEATTRACK Dashboard Suite is the visual reference: dark brown/black
navigation surrounding a warm, gold-accented operations desk. The top Reseller
panel establishes consistency. Inventory uses the bottom-left stock/movement
composition; Supervisor uses the bottom-right case/message composition. This is
a dashboard direction, not authorization to redesign unrelated portal pages.

Audience: Batangas Premium's resellers, Sales staff, Inventory staff and Owner.
English UI, Philippine peso amounts and Manila business dates follow existing
`app/business_time.py`, `app/web.py` and portal copy. No Japanese locale or
Japan-market behavior is introduced. The signature is a gold instrument tile
beside a real operational fact. Avoid generic blue admin panels, fabricated
online indicators and large zero-state cards.

Existing runtime CSS remains canonical (model B). `app/static/css/portal_base.css`
owns palette and local Rubik. `app/static/css/portals/role-dashboards.css` consumes
those variables for dashboard composition. This document mirrors accepted values;
there is no generated theme, new framework adapter or font service.

## Colors

Mapping: `primary` → `--primary`, `background` → `--surface-muted`, `surface` →
`--surface`, `text` → `--text`, `muted` → `--text-light`, `danger` → `--error`.
Other named palette entries map to same-named base variables. Borders, shadows
and `--dash-accent` derive from existing tokens. Owner dashboard aliases bring
its older grey theme into the approved cream palette, scoped to dashboards.

Gold marks active work and icon tiles. Darker accents support readable links.
Errors use red plus explicit labels; successful movements use green. Color is
supplementary to numbers and text, including chart legends. Verify future token
changes against computed styles and all five role screenshots.

## Typography

Retain locally hosted Rubik. Headings: 28–32px desktop, 24px phone. Section titles:
18px. Body: 14–16px; secondary operational metadata: 11–12px. Use medium/semibold
emphasis and tabular figures for amounts, counts and quantities. Wrap long names
and large values. Use existing currency/date formatters rather than manual strings.

## Layout

Content maximum: 1440px; section gaps: 20–22px; panel padding: 16–20px. Four summary
cards become two columns when narrow. Operational grids stack; tables become
labeled records on phones. The document owns vertical scrolling. Sidebar/profile
behavior remains unchanged. Never conceal a layout defect with hidden overflow.

Inventory: summary → stock status / expiry → movements / tasks → analytics.
Supervisor: summary → present exceptions → cases / messages → orders / shortcuts →
all queues → recent orders → collapsed financial details. Other roles retain
purchasing, assigned-work and Owner-intervention compositions using the same
panel/action language.

## Elevation & Depth

Use subtle borders and low-opacity shadows. Cream icon tiles identify work;
action lists use dividers. Avoid heavy gradients, blurred backdrops and animated
charts. White/off-white content surfaces sit quietly against the cream canvas.

## Shapes

Panels have 12px corners, controls approximately 8px and icon tiles 8–10px.
People may use circular initials. Stock rings partition product counts; never
mix packs and product counts or overlapping queues in a ring.

## Components

The Supervisor Accounts workspace uses the supplied reseller-assignment reference:
roster cards using the shared surface and border tokens, a cream corner accent in
the heading, circular cream avatars using the shared `user-round` profile placeholder,
status badges, and gold Save assignment buttons. Identity and current coverage sit
on the left; the existing native sales-leader select and action sit on the right.
Cards stack on phones. Scoped `--assignment-*` variables in
`app/static/css/portals/owner-directories.css` alias the canonical portal tokens;
the reference supplies layout while the portal supplies its muted gold, brown,
and cream colors. This workspace contains no Team accounts directory.

The return workflow extends the existing operations identity rather than the
dashboard layout. Its request and processor confirmation use shared native dialogs,
visible field errors, stable action labels and the existing form/confirmation
owners documented in `UX-CONTRACT.md`. Return-dialog `--rd-*` aliases consume the
base runtime palette on standalone Owner surfaces, so dialogs do not depend on
being nested in a dedicated detail page. The existing detail-page composition
retains its scoped appearance.

Inventory returns use a compact operational variant: full-width queue rows,
inline stock totals, one current-step summary and quiet underline tabs. Inventory
Actions prioritizes the current receipt/preparation/release task. Activity shows
short event rows; audit notes, custody records and the full workflow expand on
request. `app/static/css/portals/inventory-returns.css` consumes the existing runtime
tokens and scopes this density to Inventory returns.

Global scrollbars map `--scrollbar-thumb` to `--text-light`, track to
`--surface-muted`, hover to `--primary`, and active to `--gold-deep` in
`portal_base.css`. Standards-based scrollbar color/width and WebKit fallbacks
apply without an opt-in class; forced colors use system colors. Runtime CSS
remains canonical. Confirmations show the object, consequence and refund total;
Cancel receives initial focus. Conversion does not imply the transfer has been made.

`app/templates/portals/_dashboard_components.html` owns dashboard actions and
order rows. Navigation uses anchors; financial/chart-data disclosures use native
`details` / `summary`. Links have hover, active and visible-focus states. Zero
queues remain readable and clickable. No warehouse selector or editable checklist
is shown without a supporting workflow. Existing Lucide outline icons accompany
visible text. Motion is short and functional; reduced motion disables transitions.

Charts include readable data alternatives. Stock legend values sum to active
finished products. Supervisor order segments cover all seven assigned-order
statuses, all time. Empty panels explain where records appear. Message previews
open existing conversations; dashboard loads never mark messages read.
See `UX-CONTRACT.md` for behavior and `docs/knowledge/Role Dashboards.md` for data.

Profile password changes reuse a single staged form inside each role's existing
profile security card. Current password, email OTP, and new password stages keep
the existing Rubik, control, rule-list, error and button styles. The Owner profile
uses shrinkable grid tracks so the shared form stays inside its card on phones.
No new visual tokens or OTP overlay are introduced. Behavior is documented in
`UX-CONTRACT.md`.

Owner Inventory uses two primary workspace tabs and a quieter secondary row for
stock overview, raw materials, alerts, expired stock, replacement requests and movements. Both rows consume the
existing Owner workspace tab styles and unread badges. Stock tables and summaries
retain the existing density and runtime tokens; stock changes belong to Inventory
staff, while product-price forms remain in Products & prices.

## Do's and Don'ts

- Do put work the role can perform ahead of financial history.
- Do label units, scope, warning periods and overlapping counts honestly.
- Do preserve existing guards, destinations and transaction validation.
- Don't copy sample counts, prices, presence states or warehouse choices.
- Don't imply stock reservation from availability or adding to a cart.
- Don't apply dashboard composition changes to unrelated pages.

Owner Stock oversight includes Expired and Replacement in the same secondary tab
row. Replacement uses the existing table, filters and pagination styles; it shows
request status and contextual detail links while keeping stock operations read-only.
