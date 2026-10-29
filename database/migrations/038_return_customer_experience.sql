-- Presentation and customer follow-up records do not alter stock or settlement.
ALTER TABLE product_return_events ADD COLUMN IF NOT EXISTS customer_message text;
UPDATE product_return_events SET customer_message=remarks
WHERE action='rejected' AND customer_message IS NULL;

CREATE TABLE IF NOT EXISTS return_offer_snapshots (
    return_id bigint NOT NULL REFERENCES product_returns ON DELETE RESTRICT,
    revision integer NOT NULL CHECK(revision>0),
    credit numeric(12,2) NOT NULL,
    replacement_total numeric(12,2) NOT NULL,
    returned_items jsonb NOT NULL,
    replacement_items jsonb NOT NULL,
    is_baseline boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(return_id,revision)
);
INSERT INTO return_offer_snapshots(return_id,revision,credit,replacement_total,returned_items,replacement_items,is_baseline)
SELECT r.return_id,r.quote_revision,r.credit,r.replacement_total,
 COALESCE((SELECT jsonb_agg(jsonb_build_object('product_id',oi.product_id,'name',i.name,'quantity',l.quantity,
   'batch_code',b.batch_code,'unit_price',l.unit_price) ORDER BY l.return_line_id)
   FROM product_return_lines l JOIN fulfillment_allocations a USING(allocation_id)
   JOIN order_items oi USING(order_item_id) JOIN inventory_items i ON i.item_id=oi.product_id
   JOIN inventory_batches b USING(batch_id) WHERE l.return_id=r.return_id),'[]'::jsonb),
 COALESCE((SELECT jsonb_agg(jsonb_build_object('product_id',q.product_id,'name',i.name,'quantity',q.quantity,
   'unit_price',q.unit_price,'line_total',q.line_total) ORDER BY q.product_id)
   FROM replacement_quote_lines q JOIN inventory_items i ON i.item_id=q.product_id
   WHERE q.return_id=r.return_id AND q.revision=r.quote_revision),'[]'::jsonb),true
FROM product_returns r WHERE r.resolution='replace' AND r.quote_revision>0
ON CONFLICT DO NOTHING;
DROP TRIGGER IF EXISTS immutable_return_offer_snapshots ON return_offer_snapshots;
CREATE TRIGGER immutable_return_offer_snapshots BEFORE UPDATE OR DELETE OR TRUNCATE ON return_offer_snapshots
FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();

ALTER TABLE reseller_messages ADD COLUMN IF NOT EXISTS return_id bigint REFERENCES product_returns ON DELETE RESTRICT;
CREATE INDEX IF NOT EXISTS ix_reseller_messages_return ON reseller_messages(return_id) WHERE return_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS return_customer_followups (
    followup_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    return_id bigint NOT NULL REFERENCES product_returns ON DELETE RESTRICT,
    reported_by_account_id bigint NOT NULL REFERENCES accounts,
    report_key uuid NOT NULL UNIQUE,
    message text NOT NULL CHECK(char_length(btrim(message)) BETWEEN 1 AND 500),
    status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','resolved')),
    resolved_by_account_id bigint REFERENCES accounts,
    resolution_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    CHECK((status='open' AND resolved_at IS NULL) OR
          (status='resolved' AND resolved_at IS NOT NULL AND resolved_by_account_id IS NOT NULL
           AND char_length(btrim(resolution_message)) BETWEEN 5 AND 1000))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_return_open_followup ON return_customer_followups(return_id) WHERE status='open';
CREATE TABLE IF NOT EXISTS return_followup_messages (
    followup_id bigint NOT NULL REFERENCES return_customer_followups ON DELETE RESTRICT,
    client_message_id uuid NOT NULL UNIQUE,
    content text NOT NULL CHECK(char_length(btrim(content)) BETWEEN 1 AND 500),
    reseller_message_id bigint REFERENCES reseller_messages ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(followup_id,client_message_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_return_replacement_ack ON product_return_events(return_id)
WHERE action='replacement_received';
