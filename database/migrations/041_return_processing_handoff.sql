ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS processing_supervisor_account_id bigint REFERENCES accounts ON DELETE RESTRICT;
ALTER TABLE product_returns ADD COLUMN IF NOT EXISTS handed_off_at timestamptz;
CREATE INDEX IF NOT EXISTS ix_returns_processor ON product_returns(processing_supervisor_account_id,status,updated_at);

-- Preserve original approvals and custody. The timestamp records this migration's handoff.
UPDATE product_returns r SET handed_off_at=now(),processing_supervisor_account_id=COALESCE(
    (SELECT f.assigned_supervisor_account_id FROM order_refunds f JOIN accounts a ON a.account_id=f.assigned_supervisor_account_id
     WHERE f.return_id=r.return_id AND a.is_active AND a.team_leader_role='sales-supervisor' LIMIT 1),
    (SELECT a.account_id FROM accounts a WHERE a.is_active AND a.account_type='team_leader'
     AND a.team_leader_role='sales-supervisor' ORDER BY a.name,a.account_id LIMIT 1))
WHERE r.handed_off_at IS NULL AND r.approved_by_account_id IS NOT NULL AND r.status NOT IN ('submitted','rejected') AND (r.status<>'completed' OR EXISTS(SELECT 1 FROM return_customer_followups f WHERE f.return_id=r.return_id AND f.status='open'));

INSERT INTO product_return_events(return_id,action,remarks,customer_message)
SELECT return_id,'processing_handoff','Existing accepted case transferred during migration 041.',
    'Your accepted request is now being handled by Supervisor/Owner. Your assigned leader can track its progress.'
FROM product_returns r WHERE handed_off_at IS NOT NULL AND NOT EXISTS(SELECT 1 FROM product_return_events e WHERE e.return_id=r.return_id AND e.action='processing_handoff');

-- Each migration handoff has one durable notification per participant/processor.
WITH targets AS (
    SELECT r.return_id,a.account_id,
        CASE WHEN a.account_type='reseller' THEN 'reseller' WHEN a.account_type='owner' THEN 'owner' ELSE 'sales-supervisor' END portal
    FROM product_returns r JOIN orders o USING(order_id)
    JOIN accounts a ON a.is_active AND (a.account_id=o.created_by_account_id
        OR a.account_id=r.processing_supervisor_account_id
        OR (r.processing_supervisor_account_id IS NULL AND a.account_type='owner'))
    WHERE EXISTS(SELECT 1 FROM product_return_events e WHERE e.return_id=r.return_id
        AND e.action='processing_handoff' AND e.remarks='Existing accepted case transferred during migration 041.')
), inserted AS (
    INSERT INTO notifications(recipient_account_id,category,severity,title,message,target_url,source_type,source_id,dedupe_key)
    SELECT account_id,'order','info','Accepted return transferred to Supervisor/Owner',
        'Return #'||return_id||' is now handled by Supervisor/Owner. The assigned Sales Leader retains tracking access.',
        '/portal/'||portal||CASE WHEN portal='reseller' THEN '/history?view=returns&case=' ELSE '/returns?case=' END||return_id,
        'product_returns',return_id,'return-migration-041-'||return_id||'-'||account_id FROM targets
    ON CONFLICT(dedupe_key) DO NOTHING RETURNING notification_id,recipient_account_id
)
INSERT INTO notification_recipients(notification_id,account_id)
SELECT notification_id,recipient_account_id FROM inserted ON CONFLICT DO NOTHING;
