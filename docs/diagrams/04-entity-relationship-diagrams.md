# MEATTRACK entity-relationship diagrams

Source review: **2026-10-31**, current local `database/schema.sql` including its final `ALTER TABLE` and index definitions through migration **044**, plus the migration runner's metadata table. SQL was parsed as PostgreSQL syntax rather than inferred from screen labels.

## How to read these diagrams

- `PK` identifies primary-key columns. Multiple `PK` columns in an entity form one composite key.
- `FK` identifies declared foreign-key columns. `UK` identifies an unconditional single-column unique key, or a unique non-null value index. Composite unique constraints and general partial/expression indexes are explained in prose rather than attaching a misleading single-column UK.
- At the parent end, `||` means exactly one and `|o` means zero or one; at the child end, `o{` means zero or many and `o|` means zero or one.
- Solid relationships identify a foreign key contained in the child's primary key; dotted relationships are non-identifying foreign keys.
- Cardinality follows physical constraints, including nullable FKs. It does not express every business rule, such as requiring order items through application validation.
- Domain diagrams retain key columns plus selected business attributes. The full schema diagram includes every column and declared foreign key. Types are normalized for readability; precision, defaults, checks and trigger bodies remain authoritative in SQL.

## Coverage

The baseline declares **71 tables**; `tools/migrate_database.py` creates **1 additional metadata table**, `schema_migrations`. The complete diagram therefore contains **72 tables**. The existing `database/meattrack_erd.mmd` is an older partial view and was not used as the schema authority.

## Business-core ERD

Presentation overview. Selected entities and direct declared foreign keys are shown; support tables are expanded below. Actor edges are suppressed here to reduce crossings.

```mermaid
erDiagram

    ACCOUNTS {
        bigint account_id PK
        text account_type
        bigint reseller_id FK
    }

    INQUIRIES {
        bigint inquiry_id PK
        text status
    }

    RESELLERS {
        bigint reseller_id PK
        bigint inquiry_id FK, UK
        text business_name
    }

    INVENTORY_ITEMS {
        bigint item_id PK
        text item_type
        text name
    }

    INVENTORY_BATCHES {
        bigint batch_id PK
        bigint item_id FK
        date expiry_date
    }

    PRODUCT_RECIPES {
        bigint recipe_id PK
        bigint product_item_id FK
        bigint material_item_id FK
    }

    ORDERS {
        bigint order_id PK
        bigint reseller_id FK
        text status
    }

    ORDER_ITEMS {
        bigint order_item_id PK
        bigint order_id FK
        bigint product_id FK
        numeric quantity
    }

    FULFILLMENT_ALLOCATIONS {
        bigint allocation_id PK
        bigint order_item_id FK
        bigint batch_id FK
    }

    PRODUCT_RETURNS {
        bigint return_id PK
        bigint order_id FK
        text resolution
    }

    PRODUCT_RETURN_LINES {
        bigint return_line_id PK
        bigint return_id FK
        bigint allocation_id FK
    }

    ORDER_REFUNDS {
        bigint order_refund_id PK
        bigint order_id FK
        numeric amount
        bigint return_id FK, UK
    }

    SALES_REPORTS {
        bigint sales_report_id PK
        bigint reseller_id FK
    }

    FORECAST_RUNS {
        bigint forecast_run_id PK
        text status
        bigint retry_of_forecast_run_id FK, UK
    }

    FORECAST_RESULTS {
        bigint forecast_result_id PK
        bigint forecast_run_id FK
        bigint product_id FK
    }

    INQUIRIES |o..o| RESELLERS : "inquiry_id"
    RESELLERS |o..o{ ACCOUNTS : "reseller_id"
    INVENTORY_ITEMS ||..o{ INVENTORY_BATCHES : "item_id"
    INVENTORY_ITEMS ||..o{ PRODUCT_RECIPES : "product_item_id"
    INVENTORY_ITEMS ||..o{ PRODUCT_RECIPES : "material_item_id"
    RESELLERS |o..o{ ORDERS : "reseller_id"
    ORDERS ||..o{ ORDER_ITEMS : "order_id"
    INVENTORY_ITEMS ||..o{ ORDER_ITEMS : "product_id"
    ORDERS ||..o{ ORDER_REFUNDS : "order_id"
    RESELLERS |o..o{ SALES_REPORTS : "reseller_id"
    FORECAST_RUNS ||..o{ FORECAST_RESULTS : "forecast_run_id"
    INVENTORY_ITEMS ||..o{ FORECAST_RESULTS : "product_id"
    FORECAST_RUNS |o..o| FORECAST_RUNS : "retry_of_forecast_run_id"
    ORDER_ITEMS ||..o{ FULFILLMENT_ALLOCATIONS : "order_item_id"
    INVENTORY_BATCHES ||..o{ FULFILLMENT_ALLOCATIONS : "batch_id"
    ORDERS ||..o{ PRODUCT_RETURNS : "order_id"
    PRODUCT_RETURNS ||..o{ PRODUCT_RETURN_LINES : "return_id"
    FULFILLMENT_ALLOCATIONS ||..o{ PRODUCT_RETURN_LINES : "allocation_id"
    PRODUCT_RETURNS |o..o| ORDER_REFUNDS : "return_id"
```

[Standalone Mermaid source](mermaid/erd-core.mmd).

## Identity, onboarding and sessions

Accounts reference reseller profiles with a nullable, non-unique foreign key. A reseller profile can therefore have multiple referencing account rows at the schema level. Application provisioning normally creates a reseller login for an approved profile. Account email uniqueness uses a case-insensitive expression index.

```mermaid
erDiagram

    ACCOUNTS {
        bigint account_id PK
        text account_type
        bigint reseller_id FK
        text name
        text email
        text team_leader_role
        boolean is_active
    }

    INQUIRIES {
        bigint inquiry_id PK
        text name
        text email
        text business_name
        text message
        text status
        bigint assigned_team_leader_account_id FK
        bigint reviewed_by_account_id FK
        uuid chat_conversation_id FK, UK
    }

    RESELLERS {
        bigint reseller_id PK
        bigint inquiry_id FK, UK
        text business_name
        text email
        bigint team_leader_account_id FK
        bigint approved_by_account_id FK
    }

    DEPARTMENTS {
        bigint department_id PK
        text department_name UK
    }

    USER_CONSENTS {
        bigint user_consent_id PK
        bigint account_id FK
    }

    ACCOUNT_ACTIVATION_TOKENS {
        bigint activation_token_id PK
        bigint account_id FK
        char token_hash UK
    }

    ACCOUNT_LOGIN_OTPS {
        bigint account_login_otp_id PK
        bigint account_id FK
    }

    ACCOUNT_PASSWORD_OTPS {
        bigint account_password_otp_id PK
        bigint account_id FK
    }

    WEB_SESSIONS {
        char token_hash PK
        bigint account_id FK
    }

    ACTIVITY_LOGS {
        bigint activity_log_id PK
        bigint account_id FK
        text action
    }

    ACCOUNTS |o..o{ ACTIVITY_LOGS : "account_id"
    ACCOUNTS |o..o{ INQUIRIES : "assigned_team_leader_account_id"
    ACCOUNTS |o..o{ INQUIRIES : "reviewed_by_account_id"
    INQUIRIES |o..o| RESELLERS : "inquiry_id"
    ACCOUNTS |o..o{ RESELLERS : "team_leader_account_id"
    ACCOUNTS |o..o{ RESELLERS : "approved_by_account_id"
    RESELLERS |o..o{ ACCOUNTS : "reseller_id"
    ACCOUNTS ||..o{ USER_CONSENTS : "account_id"
    ACCOUNTS ||..o{ ACCOUNT_PASSWORD_OTPS : "account_id"
    ACCOUNTS ||..o{ ACCOUNT_LOGIN_OTPS : "account_id"
    ACCOUNTS |o..o{ WEB_SESSIONS : "account_id"
    ACCOUNTS ||..o{ ACCOUNT_ACTIVATION_TOKENS : "account_id"
```

[Standalone Mermaid source](mermaid/erd-identity.mmd).

## Cart, orders, payment and fulfillment

Paid order holds reference products, not particular batches. Fulfillment allocations connect order items to the actual batches supplied. A reseller receipt acknowledgement is optional and unique per order.

```mermaid
erDiagram

    ACCOUNTS {
        bigint account_id PK
        text account_type
        bigint reseller_id FK
        text name
        text email
        text team_leader_role
        boolean is_active
    }

    RESELLERS {
        bigint reseller_id PK
        bigint inquiry_id FK, UK
        text business_name
        text email
        bigint team_leader_account_id FK
        bigint approved_by_account_id FK
    }

    INVENTORY_ITEMS {
        bigint item_id PK
        text item_type
        text name
        text unit
        numeric base_price
        numeric quantity_available
    }

    INVENTORY_BATCHES {
        bigint batch_id PK
        bigint item_id FK
        text batch_code UK
        numeric quantity_available
        text unit
        date expiry_date
        text quality_status
    }

    RESELLER_CART_ITEMS {
        bigint cart_item_id PK
        bigint account_id FK
        bigint product_id FK
        numeric quantity
    }

    ORDERS {
        bigint order_id PK
        text order_type
        bigint reseller_id FK
        bigint created_by_account_id FK
        bigint team_leader_account_id FK
        bigint approved_by_account_id FK
        timestamptz order_date
        text status
        timestamptz fulfilled_at
        numeric total_amount
    }

    ORDER_ITEMS {
        bigint order_item_id PK
        bigint order_id FK
        bigint product_id FK
        numeric quantity
        text unit
        numeric unit_price
        numeric line_total
    }

    ORDER_PAYMENT_PROOFS {
        bigint order_payment_proof_id PK
        bigint order_id FK
        bigint uploaded_by_account_id FK
        bytea content
    }

    ORDER_STOCK_RESERVATIONS {
        bigint order_id PK, FK
        bigint product_id PK, FK
        numeric quantity
    }

    ORDER_RESERVATION_EVENTS {
        bigint reservation_event_id PK
        bigint order_id FK
        bigint product_id FK
        numeric quantity_delta
        text action
        bigint actor_account_id FK
    }

    FULFILLMENT_ALLOCATIONS {
        bigint allocation_id PK
        bigint order_item_id FK
        bigint batch_id FK
        numeric quantity
        bigint verified_by_account_id FK
    }

    RESELLER_ORDER_RECEIPTS {
        bigint order_id PK, FK
        bigint account_id FK
    }

    ACCOUNTS |o..o{ RESELLERS : "team_leader_account_id"
    ACCOUNTS |o..o{ RESELLERS : "approved_by_account_id"
    RESELLERS |o..o{ ACCOUNTS : "reseller_id"
    INVENTORY_ITEMS ||..o{ INVENTORY_BATCHES : "item_id"
    RESELLERS |o..o{ ORDERS : "reseller_id"
    ACCOUNTS |o..o{ ORDERS : "created_by_account_id"
    ACCOUNTS |o..o{ ORDERS : "team_leader_account_id"
    ACCOUNTS |o..o{ ORDERS : "approved_by_account_id"
    ORDERS ||..o{ ORDER_ITEMS : "order_id"
    INVENTORY_ITEMS ||..o{ ORDER_ITEMS : "product_id"
    ORDERS ||--o{ ORDER_STOCK_RESERVATIONS : "order_id"
    INVENTORY_ITEMS ||--o{ ORDER_STOCK_RESERVATIONS : "product_id"
    ORDERS ||..o{ ORDER_PAYMENT_PROOFS : "order_id"
    ACCOUNTS |o..o{ ORDER_PAYMENT_PROOFS : "uploaded_by_account_id"
    ACCOUNTS ||..o{ RESELLER_CART_ITEMS : "account_id"
    INVENTORY_ITEMS ||..o{ RESELLER_CART_ITEMS : "product_id"
    ORDER_ITEMS ||..o{ FULFILLMENT_ALLOCATIONS : "order_item_id"
    INVENTORY_BATCHES ||..o{ FULFILLMENT_ALLOCATIONS : "batch_id"
    ACCOUNTS |o..o{ FULFILLMENT_ALLOCATIONS : "verified_by_account_id"
    ORDERS ||..o{ ORDER_RESERVATION_EVENTS : "order_id"
    INVENTORY_ITEMS ||..o{ ORDER_RESERVATION_EVENTS : "product_id"
    ACCOUNTS |o..o{ ORDER_RESERVATION_EVENTS : "actor_account_id"
    ORDERS ||--o| RESELLER_ORDER_RECEIPTS : "order_id"
    ACCOUNTS ||..o{ RESELLER_ORDER_RECEIPTS : "account_id"
```

[Standalone Mermaid source](mermaid/erd-orders.mmd).

## Production, stock and non-sellable custody

The two recipe foreign keys both reference inventory_items: one finished product and one material. expired_holdings stores expired pull-outs, expired arrivals and reseller returns. Receipt lines uniquely link their custody holding and movement. External account/order/allocation anchors are omitted in this view.

```mermaid
erDiagram

    INVENTORY_ITEMS {
        bigint item_id PK
        text item_type
        text name
        text unit
        numeric base_price
        numeric quantity_available
    }

    INVENTORY_BATCHES {
        bigint batch_id PK
        bigint item_id FK
        text batch_code UK
        numeric quantity_available
        text unit
        date expiry_date
        text quality_status
    }

    PRODUCT_RECIPES {
        bigint recipe_id PK
        bigint product_item_id FK
        bigint material_item_id FK
        numeric quantity_required
        text unit
    }

    INVENTORY_MOVEMENTS {
        bigint movement_id PK
        bigint item_id FK
        bigint affected_batch_id FK
        bigint production_batch_id FK
        bigint order_id FK
        numeric quantity_delta
        text unit
        bigint actor_account_id FK
    }

    EXPIRED_PULLOUTS {
        bigint pullout_id PK
        bigint batch_id FK, UK
    }

    EXPIRED_HOLDINGS {
        bigint holding_id PK
        bigint product_id FK
        bigint batch_id FK
        bigint return_line_id FK
        text source
        date expiry_date
        numeric quantity_remaining
        text location
        bigint created_by_account_id FK
    }

    EXPIRED_STOCK_MOVEMENTS {
        bigint movement_id PK
        bigint holding_id FK
        text action
        numeric quantity
        bigint actor_account_id FK
        uuid operation_key UK
    }

    PRODUCT_RETURN_LINES {
        bigint return_line_id PK
        bigint return_id FK
        bigint allocation_id FK
        numeric quantity
        numeric received_quantity
        numeric unit_price
    }

    PRODUCT_RETURN_RECEIPTS {
        bigint receipt_id PK
        bigint return_id FK
        uuid operation_key UK
        bigint received_by_account_id FK
        text location
    }

    PRODUCT_RETURN_RECEIPT_LINES {
        bigint receipt_line_id PK
        bigint receipt_id FK
        bigint return_line_id FK
        bigint holding_id FK, UK
        bigint movement_id FK, UK
        numeric quantity
    }

    ALERTS {
        bigint alert_id PK
        text severity
        bigint product_id FK
        bigint product_batch_id FK
        bigint raw_material_id FK
        text message
        text status
    }

    INVENTORY_ITEMS ||..o{ INVENTORY_BATCHES : "item_id"
    INVENTORY_ITEMS ||..o{ PRODUCT_RECIPES : "product_item_id"
    INVENTORY_ITEMS ||..o{ PRODUCT_RECIPES : "material_item_id"
    INVENTORY_ITEMS |o..o{ ALERTS : "product_id"
    INVENTORY_BATCHES |o..o{ ALERTS : "product_batch_id"
    INVENTORY_ITEMS |o..o{ ALERTS : "raw_material_id"
    INVENTORY_ITEMS ||..o{ INVENTORY_MOVEMENTS : "item_id"
    INVENTORY_BATCHES |o..o{ INVENTORY_MOVEMENTS : "affected_batch_id"
    INVENTORY_BATCHES |o..o{ INVENTORY_MOVEMENTS : "production_batch_id"
    INVENTORY_BATCHES ||..o| EXPIRED_PULLOUTS : "batch_id"
    INVENTORY_ITEMS ||..o{ EXPIRED_HOLDINGS : "product_id"
    INVENTORY_BATCHES |o..o{ EXPIRED_HOLDINGS : "batch_id"
    PRODUCT_RETURN_LINES |o..o{ EXPIRED_HOLDINGS : "return_line_id"
    EXPIRED_HOLDINGS ||..o{ EXPIRED_STOCK_MOVEMENTS : "holding_id"
    PRODUCT_RETURN_RECEIPTS ||..o{ PRODUCT_RETURN_RECEIPT_LINES : "receipt_id"
    PRODUCT_RETURN_LINES ||..o{ PRODUCT_RETURN_RECEIPT_LINES : "return_line_id"
    EXPIRED_HOLDINGS ||..o| PRODUCT_RETURN_RECEIPT_LINES : "holding_id"
    EXPIRED_STOCK_MOVEMENTS ||..o| PRODUCT_RETURN_RECEIPT_LINES : "movement_id"
```

[Standalone Mermaid source](mermaid/erd-inventory.mmd).

## Return cases, receipt trace and customer follow-ups

Every return line references an original fulfillment allocation. Requested quantity and actual received quantity are distinct. Customer follow-ups and optional linked general-chat messages retain their own records. Case actor edges are expanded in the full ERD.

```mermaid
erDiagram

    ORDERS {
        bigint order_id PK
        text order_type
        bigint reseller_id FK
        bigint created_by_account_id FK
        bigint team_leader_account_id FK
        bigint approved_by_account_id FK
        timestamptz order_date
        text status
        timestamptz fulfilled_at
        numeric total_amount
    }

    ORDER_ITEMS {
        bigint order_item_id PK
        bigint order_id FK
        bigint product_id FK
        numeric quantity
        text unit
        numeric unit_price
        numeric line_total
    }

    INVENTORY_BATCHES {
        bigint batch_id PK
        bigint item_id FK
        text batch_code UK
        numeric quantity_available
        text unit
        date expiry_date
        text quality_status
    }

    FULFILLMENT_ALLOCATIONS {
        bigint allocation_id PK
        bigint order_item_id FK
        bigint batch_id FK
        numeric quantity
        bigint verified_by_account_id FK
    }

    PRODUCT_RETURNS {
        bigint return_id PK
        bigint order_id FK
        text resolution
        text status
        bigint requested_by_account_id FK
        bigint approved_by_account_id FK
        numeric credit
        integer quote_revision
        integer accepted_revision
        bigint payment_verified_by_account_id FK
        text payment_reference UK
        uuid operation_key UK
        bigint verified_by_account_id FK
        bigint settlement_authorized_by_account_id FK
        bigint processing_supervisor_account_id FK
        bigint replacement_prepared_by_account_id FK
        bigint replacement_delivered_by_account_id FK
    }

    PRODUCT_RETURN_LINES {
        bigint return_line_id PK
        bigint return_id FK
        bigint allocation_id FK
        numeric quantity
        numeric received_quantity
        numeric unit_price
    }

    PRODUCT_RETURN_RECEIPTS {
        bigint receipt_id PK
        bigint return_id FK
        uuid operation_key UK
        bigint received_by_account_id FK
        text location
    }

    PRODUCT_RETURN_RECEIPT_LINES {
        bigint receipt_line_id PK
        bigint receipt_id FK
        bigint return_line_id FK
        bigint holding_id FK, UK
        bigint movement_id FK, UK
        numeric quantity
    }

    EXPIRED_HOLDINGS {
        bigint holding_id PK
        bigint product_id FK
        bigint batch_id FK
        bigint return_line_id FK
        text source
        date expiry_date
        numeric quantity_remaining
        text location
        bigint created_by_account_id FK
    }

    EXPIRED_STOCK_MOVEMENTS {
        bigint movement_id PK
        bigint holding_id FK
        text action
        numeric quantity
        bigint actor_account_id FK
        uuid operation_key UK
    }

    RETURN_ATTACHMENTS {
        bigint attachment_id PK
        bigint return_id FK
        integer revision
        bytea content
        bigint uploaded_by_account_id FK
    }

    PRODUCT_RETURN_EVENTS {
        bigint event_id PK
        bigint return_id FK
        bigint actor_account_id FK
        text action
        uuid operation_key UK
        bigint refund_id FK
        bigint refund_attempt_id FK
        numeric amount
    }

    RETURN_CUSTOMER_FOLLOWUPS {
        bigint followup_id PK
        bigint return_id FK
        bigint reported_by_account_id FK
        uuid report_key UK
        text message
        text status
        bigint resolved_by_account_id FK
    }

    RETURN_FOLLOWUP_MESSAGES {
        bigint followup_id PK, FK
        uuid client_message_id PK
        text content
        bigint reseller_message_id FK
    }

    RESELLER_MESSAGES {
        bigint reseller_message_id PK
        bigint conversation_id FK
        bigint sender_account_id FK
        text content
        bigint return_id FK
    }

    ORDERS ||..o{ ORDER_ITEMS : "order_id"
    ORDER_ITEMS ||..o{ FULFILLMENT_ALLOCATIONS : "order_item_id"
    INVENTORY_BATCHES ||..o{ FULFILLMENT_ALLOCATIONS : "batch_id"
    ORDERS ||..o{ PRODUCT_RETURNS : "order_id"
    PRODUCT_RETURNS ||..o{ PRODUCT_RETURN_LINES : "return_id"
    FULFILLMENT_ALLOCATIONS ||..o{ PRODUCT_RETURN_LINES : "allocation_id"
    PRODUCT_RETURNS ||..o{ PRODUCT_RETURN_EVENTS : "return_id"
    PRODUCT_RETURNS ||..o{ RETURN_ATTACHMENTS : "return_id"
    INVENTORY_BATCHES |o..o{ EXPIRED_HOLDINGS : "batch_id"
    PRODUCT_RETURN_LINES |o..o{ EXPIRED_HOLDINGS : "return_line_id"
    EXPIRED_HOLDINGS ||..o{ EXPIRED_STOCK_MOVEMENTS : "holding_id"
    PRODUCT_RETURNS ||..o{ PRODUCT_RETURN_RECEIPTS : "return_id"
    PRODUCT_RETURN_RECEIPTS ||..o{ PRODUCT_RETURN_RECEIPT_LINES : "receipt_id"
    PRODUCT_RETURN_LINES ||..o{ PRODUCT_RETURN_RECEIPT_LINES : "return_line_id"
    EXPIRED_HOLDINGS ||..o| PRODUCT_RETURN_RECEIPT_LINES : "holding_id"
    EXPIRED_STOCK_MOVEMENTS ||..o| PRODUCT_RETURN_RECEIPT_LINES : "movement_id"
    PRODUCT_RETURNS |o..o{ RESELLER_MESSAGES : "return_id"
    PRODUCT_RETURNS ||..o{ RETURN_CUSTOMER_FOLLOWUPS : "return_id"
    RETURN_CUSTOMER_FOLLOWUPS ||--o{ RETURN_FOLLOWUP_MESSAGES : "followup_id"
    RESELLER_MESSAGES |o..o{ RETURN_FOLLOWUP_MESSAGES : "reseller_message_id"
```

[Standalone Mermaid source](mermaid/erd-returns.mmd).

## Replacement quotations, preparation, release and messages

The physical schema stores quote revisions as values; it does not declare a composite foreign key from quote lines to offer snapshots. Preparation and delivery metadata live on product_returns. Replacement messages and per-account read cursors are separate tables.

```mermaid
erDiagram

    PRODUCT_RETURNS {
        bigint return_id PK
        bigint order_id FK
        text resolution
        text status
        bigint requested_by_account_id FK
        bigint approved_by_account_id FK
        numeric credit
        integer quote_revision
        integer accepted_revision
        bigint payment_verified_by_account_id FK
        text payment_reference UK
        uuid operation_key UK
        bigint verified_by_account_id FK
        bigint settlement_authorized_by_account_id FK
        bigint processing_supervisor_account_id FK
        bigint replacement_prepared_by_account_id FK
        bigint replacement_delivered_by_account_id FK
    }

    INVENTORY_ITEMS {
        bigint item_id PK
        text item_type
        text name
        text unit
        numeric base_price
        numeric quantity_available
    }

    INVENTORY_BATCHES {
        bigint batch_id PK
        bigint item_id FK
        text batch_code UK
        numeric quantity_available
        text unit
        date expiry_date
        text quality_status
    }

    REPLACEMENT_QUOTE_LINES {
        bigint quote_line_id PK
        bigint return_id FK
        integer revision
        bigint product_id FK
        numeric quantity
        numeric unit_price
        numeric line_total
        jsonb pricing_components
    }

    RETURN_OFFER_SNAPSHOTS {
        bigint return_id PK, FK
        integer revision PK
        numeric credit
        numeric replacement_total
    }

    REPLACEMENT_STOCK_RESERVATIONS {
        bigint return_id PK, FK
        bigint batch_id PK, FK
        numeric quantity
    }

    REPLACEMENT_RELEASES {
        bigint release_id PK
        bigint return_id FK
        bigint batch_id FK
        numeric quantity
        bigint released_by_account_id FK
        bigint inventory_movement_id FK, UK
    }

    INVENTORY_MOVEMENTS {
        bigint movement_id PK
        bigint item_id FK
        bigint affected_batch_id FK
        bigint production_batch_id FK
        bigint order_id FK
        numeric quantity_delta
        text unit
        bigint actor_account_id FK
    }

    RETURN_CONVERSATION_MESSAGES {
        bigint message_id PK
        bigint return_id FK
        bigint sender_account_id FK
        text content
    }

    RETURN_CONVERSATION_READS {
        bigint return_id PK, FK
        bigint account_id PK, FK
        bigint last_message_id
    }

    ACCOUNTS {
        bigint account_id PK
        text account_type
        bigint reseller_id FK
        text name
        text email
        text team_leader_role
        boolean is_active
    }

    INVENTORY_ITEMS ||..o{ INVENTORY_BATCHES : "item_id"
    INVENTORY_ITEMS ||..o{ INVENTORY_MOVEMENTS : "item_id"
    INVENTORY_BATCHES |o..o{ INVENTORY_MOVEMENTS : "affected_batch_id"
    INVENTORY_BATCHES |o..o{ INVENTORY_MOVEMENTS : "production_batch_id"
    ACCOUNTS |o..o{ INVENTORY_MOVEMENTS : "actor_account_id"
    ACCOUNTS ||..o{ PRODUCT_RETURNS : "requested_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "approved_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "payment_verified_by_account_id"
    PRODUCT_RETURNS ||..o{ REPLACEMENT_QUOTE_LINES : "return_id"
    INVENTORY_ITEMS ||..o{ REPLACEMENT_QUOTE_LINES : "product_id"
    PRODUCT_RETURNS ||--o{ REPLACEMENT_STOCK_RESERVATIONS : "return_id"
    INVENTORY_BATCHES ||--o{ REPLACEMENT_STOCK_RESERVATIONS : "batch_id"
    PRODUCT_RETURNS ||..o{ REPLACEMENT_RELEASES : "return_id"
    INVENTORY_BATCHES ||..o{ REPLACEMENT_RELEASES : "batch_id"
    ACCOUNTS ||..o{ REPLACEMENT_RELEASES : "released_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "verified_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "settlement_authorized_by_account_id"
    INVENTORY_MOVEMENTS |o..o| REPLACEMENT_RELEASES : "inventory_movement_id"
    PRODUCT_RETURNS ||--o{ RETURN_OFFER_SNAPSHOTS : "return_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "processing_supervisor_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "replacement_prepared_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "replacement_delivered_by_account_id"
    PRODUCT_RETURNS ||..o{ RETURN_CONVERSATION_MESSAGES : "return_id"
    ACCOUNTS |o..o{ RETURN_CONVERSATION_MESSAGES : "sender_account_id"
    PRODUCT_RETURNS ||--o{ RETURN_CONVERSATION_READS : "return_id"
    ACCOUNTS ||--o{ RETURN_CONVERSATION_READS : "account_id"
```

[Standalone Mermaid source](mermaid/erd-replacement.mmd).

## Refund obligations, transfer attempts and evidence

orders to order_refunds is one-to-many in the final schema: the original whole-order UNIQUE was dropped. Partial unique indexes allow one non-return rejection refund per order and one refund per non-null return_id. Each transfer proof is unique per attempt. Refund operations preserve idempotent request traceability.

```mermaid
erDiagram

    ORDERS {
        bigint order_id PK
        text order_type
        bigint reseller_id FK
        bigint created_by_account_id FK
        bigint team_leader_account_id FK
        bigint approved_by_account_id FK
        timestamptz order_date
        text status
        timestamptz fulfilled_at
        numeric total_amount
    }

    PRODUCT_RETURNS {
        bigint return_id PK
        bigint order_id FK
        text resolution
        text status
        bigint requested_by_account_id FK
        bigint approved_by_account_id FK
        numeric credit
        integer quote_revision
        integer accepted_revision
        bigint payment_verified_by_account_id FK
        text payment_reference UK
        uuid operation_key UK
        bigint verified_by_account_id FK
        bigint settlement_authorized_by_account_id FK
        bigint processing_supervisor_account_id FK
        bigint replacement_prepared_by_account_id FK
        bigint replacement_delivered_by_account_id FK
    }

    ORDER_REFUNDS {
        bigint order_refund_id PK
        bigint order_id FK
        numeric amount
        char currency
        text status
        bigint requested_by_account_id FK
        bigint assigned_supervisor_account_id FK
        bigint processing_by_account_id FK
        bigint return_id FK, UK
    }

    ORDER_REFUND_ATTEMPTS {
        bigint order_refund_attempt_id PK
        bigint order_refund_id FK
        integer attempt_number
        text status
        bigint processor_account_id FK
        text refund_transaction_key UK
    }

    ORDER_REFUND_PROOFS {
        bigint order_refund_proof_id PK
        bigint order_refund_attempt_id FK, UK
        bigint uploaded_by_account_id FK
        bytea content
    }

    REFUND_OPERATIONS {
        uuid operation_key PK
        bigint refund_id FK
        bigint actor_account_id FK
        text action
        bigint attempt_id FK
    }

    ORDER_REFUND_MESSAGES {
        bigint order_refund_message_id PK
        bigint order_refund_id FK
        bigint sender_account_id FK
        text message
        text message_kind
        text system_event_key UK
    }

    REFUND_MESSAGE_READS {
        bigint order_refund_id PK, FK
        bigint account_id PK, FK
        bigint last_message_id
    }

    PRODUCT_RETURN_EVENTS {
        bigint event_id PK
        bigint return_id FK
        bigint actor_account_id FK
        text action
        uuid operation_key UK
        bigint refund_id FK
        bigint refund_attempt_id FK
        numeric amount
    }

    ACCOUNTS {
        bigint account_id PK
        text account_type
        bigint reseller_id FK
        text name
        text email
        text team_leader_role
        boolean is_active
    }

    ACCOUNTS |o..o{ ORDERS : "created_by_account_id"
    ACCOUNTS |o..o{ ORDERS : "team_leader_account_id"
    ACCOUNTS |o..o{ ORDERS : "approved_by_account_id"
    ORDERS ||..o{ ORDER_REFUNDS : "order_id"
    ACCOUNTS |o..o{ ORDER_REFUNDS : "requested_by_account_id"
    ACCOUNTS |o..o{ ORDER_REFUNDS : "assigned_supervisor_account_id"
    ACCOUNTS |o..o{ ORDER_REFUNDS : "processing_by_account_id"
    ORDER_REFUNDS ||..o{ ORDER_REFUND_ATTEMPTS : "order_refund_id"
    ACCOUNTS ||..o{ ORDER_REFUND_ATTEMPTS : "processor_account_id"
    ORDER_REFUND_ATTEMPTS ||..o| ORDER_REFUND_PROOFS : "order_refund_attempt_id"
    ACCOUNTS |o..o{ ORDER_REFUND_PROOFS : "uploaded_by_account_id"
    ORDER_REFUNDS ||..o{ ORDER_REFUND_MESSAGES : "order_refund_id"
    ACCOUNTS ||..o{ ORDER_REFUND_MESSAGES : "sender_account_id"
    ORDERS ||..o{ PRODUCT_RETURNS : "order_id"
    ACCOUNTS ||..o{ PRODUCT_RETURNS : "requested_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "approved_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "payment_verified_by_account_id"
    PRODUCT_RETURNS ||..o{ PRODUCT_RETURN_EVENTS : "return_id"
    ACCOUNTS |o..o{ PRODUCT_RETURN_EVENTS : "actor_account_id"
    PRODUCT_RETURNS |o..o| ORDER_REFUNDS : "return_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "verified_by_account_id"
    ORDER_REFUNDS |o..o{ PRODUCT_RETURN_EVENTS : "refund_id"
    ORDER_REFUND_ATTEMPTS |o..o{ PRODUCT_RETURN_EVENTS : "refund_attempt_id"
    ORDER_REFUNDS ||..o{ REFUND_OPERATIONS : "refund_id"
    ACCOUNTS ||..o{ REFUND_OPERATIONS : "actor_account_id"
    ORDER_REFUND_ATTEMPTS ||..o{ REFUND_OPERATIONS : "attempt_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "settlement_authorized_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "processing_supervisor_account_id"
    ORDER_REFUNDS ||--o{ REFUND_MESSAGE_READS : "order_refund_id"
    ACCOUNTS ||--o{ REFUND_MESSAGE_READS : "account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "replacement_prepared_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "replacement_delivered_by_account_id"
```

[Standalone Mermaid source](mermaid/erd-refunds.mmd).

## Reports and forecasting

Activity snapshots are optional one-to-one saved documents for reports; legacy reports need not have one. Membership tables preserve order/refund traceability. Forecast results and summaries reference both their run and product; a retry run can reference an earlier run.

```mermaid
erDiagram

    ACCOUNTS {
        bigint account_id PK
        text account_type
        bigint reseller_id FK
        text name
        text email
        text team_leader_role
        boolean is_active
    }

    DEPARTMENTS {
        bigint department_id PK
        text department_name UK
    }

    RESELLERS {
        bigint reseller_id PK
        bigint inquiry_id FK, UK
        text business_name
        text email
        bigint team_leader_account_id FK
        bigint approved_by_account_id FK
    }

    ORDERS {
        bigint order_id PK
        text order_type
        bigint reseller_id FK
        bigint created_by_account_id FK
        bigint team_leader_account_id FK
        bigint approved_by_account_id FK
        timestamptz order_date
        text status
        timestamptz fulfilled_at
        numeric total_amount
    }

    ORDER_REFUNDS {
        bigint order_refund_id PK
        bigint order_id FK
        numeric amount
        char currency
        text status
        bigint requested_by_account_id FK
        bigint assigned_supervisor_account_id FK
        bigint processing_by_account_id FK
        bigint return_id FK, UK
    }

    INVENTORY_ITEMS {
        bigint item_id PK
        text item_type
        text name
        text unit
        numeric base_price
        numeric quantity_available
    }

    SALES_REPORTS {
        bigint sales_report_id PK
        text report_source
        bigint submitted_by_account_id FK
        bigint reseller_id FK
        bigint department_id FK
        date period_start
        date period_end
        numeric total_sales
        integer total_orders
    }

    SALES_REPORT_ITEMS {
        bigint sales_report_item_id PK
        bigint sales_report_id FK
        bigint product_id FK
        text unit
        numeric unit_price
        numeric line_total
    }

    SALES_REPORT_ATTACHMENTS {
        bigint sales_report_attachment_id PK
        bigint sales_report_id FK
        bytea content
    }

    SALES_REPORT_ORDERS {
        bigint sales_report_id PK, FK
        bigint order_id PK, FK
    }

    SALES_REPORT_REFUNDS {
        bigint sales_report_id PK, FK
        bigint order_refund_id PK, FK
        numeric amount
    }

    SALES_REPORT_ACTIVITY_SNAPSHOTS {
        bigint sales_report_id PK, FK
        bigint author_account_id FK
        uuid submission_key UK
        smallint schema_version
        jsonb payload
    }

    FORECAST_RUNS {
        bigint forecast_run_id PK
        bigint run_by_account_id FK
        text model_name
        integer forecast_horizon_days
        text status
        integer attempt_count
        timestamptz next_attempt_at
        bigint retry_of_forecast_run_id FK, UK
    }

    FORECAST_PRODUCT_SUMMARIES {
        bigint forecast_product_summary_id PK
        bigint forecast_run_id FK
        bigint product_id FK
        text method
        text diagnostic_status
        numeric forecast_total
        numeric usable_stock
        numeric production_gap
    }

    FORECAST_RESULTS {
        bigint forecast_result_id PK
        bigint forecast_run_id FK
        bigint product_id FK
        date forecast_date
        numeric predicted_quantity
    }

    ACCOUNTS |o..o{ RESELLERS : "team_leader_account_id"
    ACCOUNTS |o..o{ RESELLERS : "approved_by_account_id"
    RESELLERS |o..o{ ACCOUNTS : "reseller_id"
    RESELLERS |o..o{ ORDERS : "reseller_id"
    ACCOUNTS |o..o{ ORDERS : "created_by_account_id"
    ACCOUNTS |o..o{ ORDERS : "team_leader_account_id"
    ACCOUNTS |o..o{ ORDERS : "approved_by_account_id"
    ORDERS ||..o{ ORDER_REFUNDS : "order_id"
    ACCOUNTS |o..o{ ORDER_REFUNDS : "requested_by_account_id"
    ACCOUNTS |o..o{ ORDER_REFUNDS : "assigned_supervisor_account_id"
    ACCOUNTS |o..o{ ORDER_REFUNDS : "processing_by_account_id"
    ACCOUNTS |o..o{ SALES_REPORTS : "submitted_by_account_id"
    RESELLERS |o..o{ SALES_REPORTS : "reseller_id"
    DEPARTMENTS |o..o{ SALES_REPORTS : "department_id"
    SALES_REPORTS ||..o{ SALES_REPORT_ITEMS : "sales_report_id"
    INVENTORY_ITEMS ||..o{ SALES_REPORT_ITEMS : "product_id"
    SALES_REPORTS ||..o{ SALES_REPORT_ATTACHMENTS : "sales_report_id"
    ACCOUNTS |o..o{ FORECAST_RUNS : "run_by_account_id"
    FORECAST_RUNS ||..o{ FORECAST_PRODUCT_SUMMARIES : "forecast_run_id"
    INVENTORY_ITEMS ||..o{ FORECAST_PRODUCT_SUMMARIES : "product_id"
    FORECAST_RUNS ||..o{ FORECAST_RESULTS : "forecast_run_id"
    INVENTORY_ITEMS ||..o{ FORECAST_RESULTS : "product_id"
    FORECAST_RUNS |o..o| FORECAST_RUNS : "retry_of_forecast_run_id"
    SALES_REPORTS ||--o{ SALES_REPORT_ORDERS : "sales_report_id"
    ORDERS ||--o{ SALES_REPORT_ORDERS : "order_id"
    SALES_REPORTS ||--o{ SALES_REPORT_REFUNDS : "sales_report_id"
    ORDER_REFUNDS ||--o{ SALES_REPORT_REFUNDS : "order_refund_id"
    SALES_REPORTS ||--o| SALES_REPORT_ACTIVITY_SNAPSHOTS : "sales_report_id"
    ACCOUNTS ||..o{ SALES_REPORT_ACTIVITY_SNAPSHOTS : "author_account_id"
```

[Standalone Mermaid source](mermaid/erd-reporting.mmd).

## Public chat, internal conversations and notifications

Public chat, internal reseller chat and order system messages are separate stores. Replacement case chat and refund conversations appear in their domain ERDs. Client UUIDs and last-read message numbers do not create undeclared foreign keys.

```mermaid
erDiagram

    ACCOUNTS {
        bigint account_id PK
        text account_type
        bigint reseller_id FK
        text name
        text email
        text team_leader_role
        boolean is_active
    }

    CHAT_CONVERSATIONS {
        uuid conversation_id PK
        text status
        bigint assigned_team_leader_account_id FK
    }

    CHAT_MESSAGES {
        bigint chat_message_id PK
        uuid conversation_id FK
        bigint sender_account_id FK
        text content
        text publication_status
    }

    CHAT_SERVICE_RATINGS {
        uuid conversation_id PK, FK
        bigint team_leader_account_id FK
    }

    TEAM_LEADER_PRESENCE {
        bigint account_id PK, FK
        uuid active_conversation_id FK
    }

    RESELLER_CONVERSATIONS {
        bigint conversation_id PK
        bigint reseller_account_id FK
        bigint team_leader_account_id FK
    }

    RESELLER_MESSAGES {
        bigint reseller_message_id PK
        bigint conversation_id FK
        bigint sender_account_id FK
        text content
        bigint return_id FK
    }

    RESELLER_MESSAGE_ATTACHMENTS {
        bigint attachment_id PK
        bigint reseller_message_id FK
        bytea content
    }

    PRODUCT_RETURNS {
        bigint return_id PK
        bigint order_id FK
        text resolution
        text status
        bigint requested_by_account_id FK
        bigint approved_by_account_id FK
        numeric credit
        integer quote_revision
        integer accepted_revision
        bigint payment_verified_by_account_id FK
        text payment_reference UK
        uuid operation_key UK
        bigint verified_by_account_id FK
        bigint settlement_authorized_by_account_id FK
        bigint processing_supervisor_account_id FK
        bigint replacement_prepared_by_account_id FK
        bigint replacement_delivered_by_account_id FK
    }

    NOTIFICATIONS {
        bigint notification_id PK
        bigint recipient_account_id FK
        text severity
        text message
        text dedupe_key UK
    }

    NOTIFICATION_RECIPIENTS {
        bigint notification_id PK, FK
        bigint account_id PK, FK
    }

    ORDER_SYSTEM_MESSAGES {
        bigint order_system_message_id PK
        bigint reseller_account_id FK
        bigint order_id FK
        text event_type
        text message
        text dedupe_key UK
        bigint order_refund_attempt_id FK
    }

    ORDERS {
        bigint order_id PK
        text order_type
        bigint reseller_id FK
        bigint created_by_account_id FK
        bigint team_leader_account_id FK
        bigint approved_by_account_id FK
        timestamptz order_date
        text status
        timestamptz fulfilled_at
        numeric total_amount
    }

    ORDER_REFUND_ATTEMPTS {
        bigint order_refund_attempt_id PK
        bigint order_refund_id FK
        integer attempt_number
        text status
        bigint processor_account_id FK
        text refund_transaction_key UK
    }

    ACCOUNTS |o..o{ ORDERS : "created_by_account_id"
    ACCOUNTS |o..o{ ORDERS : "team_leader_account_id"
    ACCOUNTS |o..o{ ORDERS : "approved_by_account_id"
    ACCOUNTS ||..o{ ORDER_REFUND_ATTEMPTS : "processor_account_id"
    ACCOUNTS |o..o{ NOTIFICATIONS : "recipient_account_id"
    ACCOUNTS ||..o{ ORDER_SYSTEM_MESSAGES : "reseller_account_id"
    ORDERS ||..o{ ORDER_SYSTEM_MESSAGES : "order_id"
    ORDER_REFUND_ATTEMPTS |o..o{ ORDER_SYSTEM_MESSAGES : "order_refund_attempt_id"
    NOTIFICATIONS ||--o{ NOTIFICATION_RECIPIENTS : "notification_id"
    ACCOUNTS ||--o{ NOTIFICATION_RECIPIENTS : "account_id"
    ACCOUNTS |o..o{ CHAT_CONVERSATIONS : "assigned_team_leader_account_id"
    CHAT_CONVERSATIONS ||..o{ CHAT_MESSAGES : "conversation_id"
    ACCOUNTS |o..o{ CHAT_MESSAGES : "sender_account_id"
    ACCOUNTS ||..o{ RESELLER_CONVERSATIONS : "reseller_account_id"
    ACCOUNTS ||..o{ RESELLER_CONVERSATIONS : "team_leader_account_id"
    RESELLER_CONVERSATIONS ||..o{ RESELLER_MESSAGES : "conversation_id"
    ACCOUNTS ||..o{ RESELLER_MESSAGES : "sender_account_id"
    CHAT_CONVERSATIONS ||--o| CHAT_SERVICE_RATINGS : "conversation_id"
    ACCOUNTS ||..o{ CHAT_SERVICE_RATINGS : "team_leader_account_id"
    ACCOUNTS ||--o| TEAM_LEADER_PRESENCE : "account_id"
    CHAT_CONVERSATIONS |o..o{ TEAM_LEADER_PRESENCE : "active_conversation_id"
    ORDERS ||..o{ PRODUCT_RETURNS : "order_id"
    ACCOUNTS ||..o{ PRODUCT_RETURNS : "requested_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "approved_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "payment_verified_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "verified_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "settlement_authorized_by_account_id"
    PRODUCT_RETURNS |o..o{ RESELLER_MESSAGES : "return_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "processing_supervisor_account_id"
    RESELLER_MESSAGES ||..o{ RESELLER_MESSAGE_ATTACHMENTS : "reseller_message_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "replacement_prepared_by_account_id"
    ACCOUNTS |o..o{ PRODUCT_RETURNS : "replacement_delivered_by_account_id"
```

[Standalone Mermaid source](mermaid/erd-messaging.mmd).

## Operational settings, delivery and migration metadata

Tables without relational links are deliberately retained. Outbox payloads and generic source IDs do not become foreign keys merely because application code interprets them. owner_step_up_challenges remains in the schema even though the current Owner portal no longer requires its separate email challenge. schema_migrations is created by the migration runner.

```mermaid
erDiagram

    ACCOUNTS {
        bigint account_id PK
        text account_type
        bigint reseller_id FK
        text name
        text email
        text team_leader_role
        boolean is_active
    }

    NOTIFICATIONS {
        bigint notification_id PK
        bigint recipient_account_id FK
        text severity
        text message
        text dedupe_key UK
    }

    NOTIFICATION_RECIPIENTS {
        bigint notification_id PK, FK
        bigint account_id PK, FK
    }

    NOTIFICATION_OUTBOX {
        bigint outbox_id PK
        text event_type
        jsonb payload
        text dedupe_key UK
        integer attempt_count
        timestamptz next_attempt_at
        timestamptz sent_at
    }

    BUSINESS_SETTINGS {
        text setting_key PK
        jsonb setting_value
        bigint updated_by_account_id FK
    }

    SYSTEM_STATUS_REPORTS {
        text component PK
        text status
    }

    SECURITY_EVENTS {
        bigint security_event_id PK
        text event_type
    }

    RATE_LIMIT_BUCKETS {
        text event_type PK
        text subject_hash PK
        integer window_seconds PK
        timestamptz bucket_started_at PK
    }

    OWNER_STEP_UP_CHALLENGES {
        bigint owner_step_up_challenge_id PK
        bigint account_id FK
        integer attempt_count
    }

    SCHEMA_MIGRATIONS {
        text version PK
    }

    ACCOUNTS |o..o{ NOTIFICATIONS : "recipient_account_id"
    NOTIFICATIONS ||--o{ NOTIFICATION_RECIPIENTS : "notification_id"
    ACCOUNTS ||--o{ NOTIFICATION_RECIPIENTS : "account_id"
    ACCOUNTS ||..o{ OWNER_STEP_UP_CHALLENGES : "account_id"
    ACCOUNTS |o..o{ BUSINESS_SETTINGS : "updated_by_account_id"
```

[Standalone Mermaid source](mermaid/erd-operations.mmd).

## Complete physical schema

The full ERD is intentionally separate because its 72 tables are unsuitable for a presentation slide:

- [Complete Mermaid source](mermaid/erd-full-schema.mmd)
- [Rendered full-schema SVG](rendered/erd-full-schema.svg)
- [Schema inventory and foreign-key details](schema-inventory.json)

Its disconnected entities are real tables, not missing relationships. Logical links inside JSON snapshots, generic audit `entity_id` values, settings payloads and message read cursors are not drawn as invented foreign keys.

## Important schema and workflow distinctions

1. **One reseller does not imply exactly one account at the database level.** `accounts.reseller_id` is not unique.
2. **One order may have several refund records.** The old unconditional unique `order_id` constraint is removed later in the schema; conditional uniqueness distinguishes rejection refunds from return-case refunds.
3. **Products and materials are one entity type with a discriminator.** Their quantities and workflows differ, and finished goods have individual batch rows.
4. **Stock reservation and physical movement are different records.** Reservation events do not deduct warehouse stock.
5. **Returns do not replenish sellable batches.** Their physical receipt is tracked through holdings, custody movements and receipt-line links.
6. **A saved report is not a live query result.** New report content is frozen in `sales_report_activity_snapshots.payload`.
7. **A read cursor is not necessarily a foreign key.** Numeric message IDs without REFERENCES are retained as attributes.
8. **A retained table is not proof of an active UI module.** For example, legacy step-up and reseller-report structures remain in the schema.

## Source basis

[`database/schema.sql`](../../database/schema.sql), [`database/migrations/`](../../database/migrations/), [`tools/migrate_database.py`](../../tools/migrate_database.py), and the repositories cited in [module flowcharts](03-module-flowcharts.md). This is a static schema analysis; no live production database was inspected.
