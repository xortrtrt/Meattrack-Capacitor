# MEATTRACK diagram pack

Analyzed on **2026-10-31** against the current local working tree, including baseline schema and migrations through **044**. This pack contains **42 Mermaid diagrams**, with editable `.mmd` sources and rendered SVG copies.

## Open the diagrams

| Requested material | Document | Contents |
| --- | --- | --- |
| Conceptual framework / IPO | [Conceptual framework and IPO](01-conceptual-framework.md) | Conceptual model, detailed IPO and common controls |
| System architecture at different levels | [System architecture](02-system-architecture.md) | Level 1 context, Level 2 containers, Level 3 components, Level 4 transaction detail, logical layers and deployment |
| Flowchart of each module | [Module flowcharts](03-module-flowcharts.md) | 18 functional modules plus Owner, Sales/Supervisor, Inventory and Reseller portal summaries |
| ERD | [Entity-relationship diagrams](04-entity-relationship-diagrams.md) | Business core, nine domain views and separate full physical schema |

For an offline visual overview, open [the rendered diagram gallery](gallery.html). Each gallery entry links to its SVG and editable Mermaid source. The Markdown documents contain the explanations, scope and repository source references.

## Recommended use for a capstone manuscript or presentation

1. Use the **conceptual framework** and **IPO** in the framework section. Their outputs describe implemented information and records, not experimentally proven improvements.
2. Use **architecture Levels 1 and 2** for an overview; use **Level 3**, the transaction sequence, and deployment view for technical discussion.
3. Use the **18 module flowcharts** for detailed process documentation. The four role summaries provide shorter walkthroughs for portal demonstrations.
4. Use the **business-core ERD** for the main presentation and domain ERDs for detail. Put the **72-table physical ERD** in an appendix or technical reference.

Suggested figure captions: “Conceptual Framework of MEATTRACK,” “Input–Process–Output Model of MEATTRACK,” “MEATTRACK System Context,” “MEATTRACK Runtime Container Architecture,” and “MEATTRACK Business-Core Entity–Relationship Diagram.”

## Interpretation and verification

- MEATTRACK is a server-rendered FastAPI application with vanilla JavaScript and Jinja2, PostgreSQL 16, and a separately running Python worker.
- Diagrams reflect actual repository workflows, including paid-only order reservations, five-day return eligibility, separate non-sellable custody and version-2 replacements with payment before preparation.
- The ERD includes **71 baseline tables plus `schema_migrations`**, **638 columns** and **147 declared foreign-key relationships**. Domain diagrams intentionally select attributes and entities for readability.
- The existing `database/meattrack_erd.mmd` is an older partial diagram; this pack documents the current baseline rather than overwriting the historical file.
- All 42 diagrams were parsed and rendered using Mermaid CLI. [Render validation](render-validation.json) records the per-diagram result. [Schema inventory](schema-inventory.json) records extracted schema metadata and the analyzed baseline's SHA-256 digest.
- Production deployment was not inspected, application tests were not run, and source review does not prove the deployed database matches the local files.
- Existing local application changes were used as analysis input. No application or database behavior was changed for this diagram pack.

## Files

- [`mermaid/`](mermaid/): one editable source per diagram.
- [`rendered/`](rendered/): SVG renderings and selected PNG previews.
- [`diagram-manifest.json`](diagram-manifest.json): diagram names and source documents.
- [`schema-inventory.json`](schema-inventory.json): table/column/key metadata, without customer records or credential values.
- [`render-validation.json`](render-validation.json): diagram rendering results.

The `.md` and `.mmd` files can be used in Mermaid-capable Markdown tools, including Obsidian. SVGs are preferable for scaling and manuscript insertion. Recheck diagram claims and regenerate affected sources when application code or schema changes.

Related project notes: [[Architecture]], [[Orders and Inventory]], [[Replacement Workflow]], [[Returns and Financials]], [[Sales Activity Reports]], [[Background Work and Integrations]], [[Database and Operations]], [[System Diagrams]].
