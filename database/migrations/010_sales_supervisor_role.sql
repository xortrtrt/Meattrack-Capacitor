ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_team_leader_role_check;

ALTER TABLE accounts
    ADD CONSTRAINT accounts_team_leader_role_check
    CHECK (team_leader_role IS NULL OR team_leader_role IN ('inventory', 'sales', 'sales-supervisor'));
