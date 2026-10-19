CREATE TABLE IF NOT EXISTS order_system_messages (
    order_system_message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reseller_account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    order_id bigint NOT NULL REFERENCES orders(order_id) ON UPDATE CASCADE ON DELETE CASCADE,
    event_type text NOT NULL CHECK (event_type IN ('approved', 'rejected', 'fulfilled')),
    title text NOT NULL CHECK (btrim(title) <> ''),
    message text NOT NULL CHECK (btrim(message) <> ''),
    rejection_reason text,
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (reseller_account_id, order_id, event_type),
    CHECK (
        (event_type = 'rejected' AND char_length(btrim(rejection_reason)) BETWEEN 5 AND 400)
        OR (event_type <> 'rejected' AND rejection_reason IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS ix_order_system_messages_reseller_created
    ON order_system_messages (reseller_account_id, created_at DESC, order_system_message_id DESC);
CREATE INDEX IF NOT EXISTS ix_order_system_messages_order
    ON order_system_messages (order_id, event_type);

INSERT INTO order_system_messages (
    reseller_account_id,
    order_id,
    event_type,
    title,
    message,
    rejection_reason,
    read_at,
    created_at
)
SELECT
    nr.account_id,
    o.order_id,
    CASE lower(n.title)
        WHEN 'order approved' THEN 'approved'
        WHEN 'order rejected' THEN 'rejected'
        WHEN 'order fulfilled' THEN 'fulfilled'
    END,
    n.title,
    CASE lower(n.title)
        WHEN 'order approved' THEN 'Your reseller order #' || o.order_id || ' was approved and is ready for fulfillment.'
        WHEN 'order rejected' THEN 'Your reseller order #' || o.order_id || ' was rejected.'
        WHEN 'order fulfilled' THEN 'Your reseller order #' || o.order_id || ' was fulfilled and recorded as a completed sale.'
    END,
    CASE
        WHEN lower(n.title) = 'order rejected' AND char_length(btrim(COALESCE(o.rejection_reason, ''))) >= 5
        THEN left(btrim(o.rejection_reason), 400)
        WHEN lower(n.title) = 'order rejected' AND char_length(btrim(COALESCE(o.notes, ''))) >= 5
        THEN left(btrim(o.notes), 400)
        WHEN lower(n.title) = 'order rejected'
        THEN 'No rejection reason was recorded.'
        ELSE NULL
    END,
    nr.read_at,
    n.created_at
FROM notifications n
JOIN notification_recipients nr ON nr.notification_id = n.notification_id
JOIN accounts a ON a.account_id = nr.account_id AND a.account_type = 'reseller'
JOIN orders o ON o.order_id = n.source_id
WHERE n.category = 'order'
  AND n.source_type = 'orders'
  AND lower(n.title) IN ('order approved', 'order rejected', 'order fulfilled')
ON CONFLICT DO NOTHING;

UPDATE notifications n
SET source_type = 'order_system_messages',
    source_id = osm.order_system_message_id,
    target_url = '/portal/reseller/inbox?system_message=' || osm.order_system_message_id
        || '#system-message-' || osm.order_system_message_id
FROM notification_recipients nr,
     order_system_messages osm
WHERE nr.notification_id = n.notification_id
  AND nr.account_id = osm.reseller_account_id
  AND n.category = 'order'
  AND n.source_type = 'orders'
  AND n.source_id = osm.order_id
  AND (
      (lower(n.title) = 'order approved' AND osm.event_type = 'approved')
      OR (lower(n.title) = 'order rejected' AND osm.event_type = 'rejected')
      OR (lower(n.title) = 'order fulfilled' AND osm.event_type = 'fulfilled')
  );
