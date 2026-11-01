---
tags: [meattrack, architecture, diagrams, documentation]
verified: 2026-10-31
---
# System diagrams

[The Mermaid diagram pack](../diagrams/README.md) documents the current local
working tree: conceptual framework and IPO; context, runtime, component,
transaction, layer and deployment views; 18 module flows and four portal
summaries; and core, domain and complete physical ERDs. Individual Mermaid sources
and SVG renderings are included for reuse.

The schema analysis checks the final baseline's CREATE/ALTER/index definitions,
including migration-044 additions. It covers 71 baseline tables plus the migration
runner's `schema_migrations` metadata table, with 147 declared foreign keys.
The older `database/meattrack_erd.mmd` is a partial historical view. Do not infer
current schema coverage or cardinality from that older diagram.

The diagram pack distinguishes physical database constraints from application
workflow rules, and source-derived configuration from verified deployment state.
It records no production inspection or application regression run. Refresh the
affected diagrams when the related implementation changes; do not treat them as
an additional source of business authority.

Related: [[Architecture]], [[Roles and Access]], [[Orders and Inventory]],
[[Returns and Financials]], [[Replacement Workflow]], [[Sales Activity Reports]],
[[Background Work and Integrations]], [[Database and Operations]],
[[Questions and Contradictions]].

Sources: `app/application.py`, `app/routers/`, `app/portals/`, `app/repositories/`,
`app/worker.py`, `app/forecasting.py`, `database/schema.sql`,
`database/migrations/044_replacement_workflow.sql`, `tools/migrate_database.py`,
`compose.prod.yml`, `deploy/nginx/meattrack.conf`.
