# MEATTRACK Documentation Diagrams

These diagrams document the implemented MEATTRACK stakeholder interactions,
business workflow, deployment architecture, and Supabase PostgreSQL data model.
The Mermaid files in `src/` are the editable sources; matching SVG and
high-resolution PNG files are generated in `output/`.

## Artifacts

| Guideline | Primary diagram | Purpose |
|---|---|---|
| People | `use-case-diagram` | Shows what each stakeholder can do and which external services support those use cases. |
| Process | `system-flowchart` | Follows reseller qualification, onboarding, ordering, payment verification, inventory production, reporting, and forecasting. |
| Technology | `system-architecture` | Shows the browser/Capacitor clients, Render-hosted FastAPI application, Supabase services, and external APIs. |
| Data | `core-erd` | Presentation-friendly view of the main business entities and relationships. |
| Data appendix | `full-technical-erd` | Complete technical view of all 23 tables declared in `database/schema.sql`. |

## Actors

- **Potential reseller:** public visitor who explores the landing page and may
  submit a qualified reseller inquiry through the chatbot.
- **Reseller:** approved partner who browses products, manages a cart, submits
  orders and payment proofs, tracks order status, and maintains a profile.
- **Sales team leader:** reviews only assigned reseller inquiries and orders,
  approves reseller accounts, monitors reseller purchases, and reports to the
  owner.
- **Inventory team leader:** manages raw materials, recipes, production batches,
  finished stock, expiry information, and inventory-only logs.
- **Owner:** manages accounts, reseller assignments, product prices, executive
  analytics, reports, and Prophet forecasts.
- **Brevo / OpenRouter:** external HTTPS services for transactional email and
  optional AI-assisted chatbot responses.

## Notation and implementation notes

- `PK`, `FK`, and `UK` mean primary key, foreign key, and unique key.
- Optional relationships match nullable foreign keys in the implemented schema.
- `inventory_items.item_type` distinguishes `raw_material` from
  `finished_product`; `product_recipes` links both roles back to the same table.
- `notifications.source_type/source_id` and activity-log entity fields are
  polymorphic application references, not database foreign keys, so the ERDs do
  not draw invented relationships for them.
- `media_assets` appears only in the full ERD. It is a dormant rollback table;
  production product and branding images are delivered from Supabase Storage.
- The removed reseller sales-report page and Owner audit-log page are not shown
  as user-facing use cases. Their retained support tables remain visible in the
  full technical ERD.
- Browser and Capacitor clients never receive Supabase database credentials.
  FastAPI owns all PostgreSQL access through the server-side psycopg2 pool.

## Regenerate exports

Install the pinned development dependencies and run:

```powershell
npm.cmd install
npm.cmd run diagrams:build
```

The renderer creates both SVG and 2x-scale PNG versions. SVG is recommended for
documents and presentation slides because it remains sharp when enlarged; PNG
is included for software that cannot import SVG.
