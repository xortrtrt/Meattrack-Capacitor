-- Optional per-message context; existing messages and pair identity remain unchanged.
ALTER TABLE reseller_messages ADD COLUMN IF NOT EXISTS order_id bigint REFERENCES orders(order_id);
