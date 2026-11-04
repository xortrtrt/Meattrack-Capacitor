CREATE TABLE forecast_demand_seed_datasets (
    dataset_key text PRIMARY KEY,
    source_name text NOT NULL,
    source_order_count integer NOT NULL CHECK (source_order_count > 0),
    source_period_start date NOT NULL,
    source_period_end date NOT NULL,
    team_leader_account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    imported_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(dataset_key) <> ''),
    CHECK (btrim(source_name) <> ''),
    CHECK (source_period_end >= source_period_start)
);

CREATE TABLE forecast_demand_seed_daily (
    dataset_key text NOT NULL REFERENCES forecast_demand_seed_datasets(dataset_key) ON UPDATE CASCADE ON DELETE CASCADE,
    source_product_name text NOT NULL,
    product_id bigint REFERENCES inventory_items(item_id) ON UPDATE CASCADE ON DELETE SET NULL,
    sale_date date NOT NULL,
    quantity numeric(14,3) NOT NULL CHECK (quantity > 0 AND quantity = trunc(quantity)),
    PRIMARY KEY (dataset_key, source_product_name, sale_date),
    CHECK (btrim(source_product_name) <> '')
);

CREATE INDEX ix_forecast_demand_seed_daily_product_date
    ON forecast_demand_seed_daily (product_id, sale_date)
    WHERE product_id IS NOT NULL;
