# MEATTRACK conceptual framework and IPO

Source review: **2026-10-31**, current local working tree, including migrations through **044**. These diagrams describe implemented software responsibilities; they do not establish the production server's deployed revision or measure business improvements.

## Conceptual framework

MEATTRACK connects business records, controlled transactions, and management information. The feedback arrow represents human planning and operational decisions; forecasts do not automatically create production orders or purchase materials.

```mermaid
flowchart LR
    subgraph I[Business inputs]
        I1[Customer inquiries and account information]
        I2[Orders and payment evidence]
        I3[Products, recipes, materials and batches]
        I4[Return evidence and actual goods received]
        I5[Recorded sales history and business settings]
    end
    subgraph P[MEATTRACK processing]
        P1[Authenticate and authorize each actor]
        P2[Coordinate onboarding, sales and inventory]
        P3[Trace batches, returns and financial settlement]
        P4[Generate reports, alerts and demand forecasts]
        P1 --> P2 --> P3 --> P4
    end
    subgraph O[System outputs]
        O1[Activated and assigned accounts]
        O2[Tracked orders and current stock availability]
        O3[Traceable custody, replacements and refunds]
        O4[Saved reports, notifications and forecast results]
    end
    I --> P --> O
    O --> D[Owner and staff review results]
    D -.->|Adjust plans and authorized settings| I
```

The intended contribution is coordinated ordering, inventory traceability, after-sales handling, and decision support. Claims such as reduced spoilage, improved forecast accuracy, or faster processing require separate evaluation data and are not established by source inspection.

## Input-process-output model

```mermaid
flowchart LR
    subgraph INPUT[INPUT]
        A["Account details and consent<br/>Reseller inquiries<br/>Orders and payment evidence<br/>Materials, recipes and batch dates<br/>Return evidence and product choices<br/>Sales history and planning parameters"]
    end
    subgraph PROCESS[PROCESS]
        B["Authenticate and authorize<br/>Review and activate accounts<br/>Process orders and verify payment<br/>Produce goods and allocate FEFO stock<br/>Verify custody and settle returns<br/>Record activity, report and forecast"]
    end
    subgraph OUTPUT[OUTPUT]
        C["Activated and assigned accounts<br/>Inquiry and order decisions<br/>Fulfillment and receipt records<br/>Stock balances, movements and alerts<br/>Replacement and refund outcomes<br/>Saved reports, exports and forecasts"]
    end
    A --> B --> C
```

| Input category | Main process | Concrete output |
| --- | --- | --- |
| Identity and consent | Password validation, optional login OTP, database session, capability and ownership checks | Authenticated role-scoped session and consent history |
| Prospective reseller details | Validate inquiry, assign Supervisor, review and provision | Inquiry decision, reseller profile, portal account, activation email queue |
| Selected products and payment evidence | Calculate server prices, recognize receipt details, validate payment, reserve available stock, review and fulfill | Order, proof records, reservations, batch allocations and system messages |
| Materials, recipes and production quantities | Validate units, lock material balances, consume recipe requirements and create batch | Material deductions, finished batch, immutable movement records |
| Return selections and supporting photos | Validate 120-hour window, trace original allocations, review, receive complete goods and settle | Non-sellable custody, refund or replacement records, event history |
| Personally handled order activity and report dates | Build consistent preview, revalidate and freeze submission | Orders, Sales, Returns and Refunds document; CSV, Excel and print views |
| Fulfilled-sales history, usable stock and forecast horizon | Queue worker job, compare eligible models, store diagnostics and results | Daily demand estimates, uncertainty where available and production gap |

## Cross-cutting controls

```mermaid
flowchart TB
    A[User action] --> B[Session and capability checks]
    B --> C[Ownership and assignment checks]
    C --> D[Input and business-rule validation]
    D --> E[Database transaction and stock or case locks]
    E --> F[Business records and applicable audit or event records]
    F --> G[Response, in-portal notification or queued delivery]
    X[CSRF and rate controls where configured] -.-> B
    Y[Operation keys and unique constraints where implemented] -.-> E
    Z[Database constraints and immutable ledger guards] -.-> F
```

## Source basis

- Application assembly: [`app/application.py`](../../app/application.py).
- Identity and scope: [`app/routers/auth.py`](../../app/routers/auth.py), [`app/capabilities.py`](../../app/capabilities.py), [`app/portal_dependencies.py`](../../app/portal_dependencies.py).
- Business processing: [`app/repositories/orders.py`](../../app/repositories/orders.py), [`app/repositories/inventory/`](../../app/repositories/inventory/), [`app/repositories/product_returns.py`](../../app/repositories/product_returns.py), [`app/repositories/replacement_workflow.py`](../../app/repositories/replacement_workflow.py), [`app/repositories/refunds.py`](../../app/repositories/refunds.py).
- Information outputs: [`app/repositories/activity_reports.py`](../../app/repositories/activity_reports.py), [`app/forecasting.py`](../../app/forecasting.py), [`app/worker.py`](../../app/worker.py).
