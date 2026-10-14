CREATE OR REPLACE FUNCTION validate_order_team_leader_snapshot()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.order_type = 'reseller' AND NOT EXISTS (
        SELECT 1
        FROM accounts a
        WHERE a.account_id = NEW.team_leader_account_id
          AND a.account_type = 'team_leader'
          AND a.team_leader_role IN ('sales', 'sales-supervisor')
          AND a.is_active = true
    ) THEN
        RAISE EXCEPTION 'Reseller orders require an active sales team leader ownership snapshot.';
    END IF;
    RETURN NEW;
END $$;
