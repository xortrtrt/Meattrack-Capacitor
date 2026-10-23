-- Migration 024 named the old constraint explicitly, but PostgreSQL's generated
-- name can be truncated differently. Match its columns instead of its name.
DO $$
DECLARE
    obsolete_constraint record;
BEGIN
    FOR obsolete_constraint IN
        SELECT c.conname
        FROM pg_constraint c
        WHERE c.conrelid = 'order_system_messages'::regclass
          AND c.contype = 'u'
          AND (
              SELECT array_agg(a.attname::text ORDER BY a.attname::text)
              FROM unnest(c.conkey) AS k(attnum)
              JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.attnum
          ) = ARRAY['event_type', 'order_id', 'reseller_account_id']::text[]
    LOOP
        EXECUTE format('ALTER TABLE order_system_messages DROP CONSTRAINT %I', obsolete_constraint.conname);
    END LOOP;
END $$;

-- Retain per-operation deduplication, including distinct refund attempts.
CREATE UNIQUE INDEX IF NOT EXISTS ux_order_system_messages_dedupe
    ON order_system_messages (dedupe_key);
