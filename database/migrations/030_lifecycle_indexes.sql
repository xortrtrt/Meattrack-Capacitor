CREATE INDEX IF NOT EXISTS ix_allocations_batch ON fulfillment_allocations(batch_id);
CREATE INDEX IF NOT EXISTS ix_returns_status_updated ON product_returns(status,updated_at);
CREATE INDEX IF NOT EXISTS ix_return_events_case ON product_return_events(return_id,event_id);
CREATE INDEX IF NOT EXISTS ix_holdings_product ON expired_holdings(product_id) WHERE quantity_remaining>0;
CREATE INDEX IF NOT EXISTS ix_replacement_reservations_batch ON replacement_stock_reservations(batch_id);
