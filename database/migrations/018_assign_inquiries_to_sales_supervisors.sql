WITH supervisors AS (
    SELECT account_id,
           row_number() OVER (ORDER BY account_id) AS supervisor_number
    FROM accounts
    WHERE account_type = 'team_leader'
      AND team_leader_role = 'sales-supervisor'
      AND is_active = true
),
open_inquiries AS (
    SELECT inquiry_id,
           row_number() OVER (ORDER BY created_at, inquiry_id) AS inquiry_number,
           (SELECT count(*) FROM supervisors) AS supervisor_count
    FROM inquiries
    WHERE status IN ('pending', 'assigned', 'contacted')
),
assignments AS (
    SELECT inquiry.inquiry_id, supervisor.account_id
    FROM open_inquiries inquiry
    JOIN supervisors supervisor
      ON supervisor.supervisor_number = ((inquiry.inquiry_number - 1) % NULLIF(inquiry.supervisor_count, 0)) + 1
)
UPDATE inquiries inquiry
SET assigned_team_leader_account_id = assignments.account_id
FROM assignments
WHERE inquiry.inquiry_id = assignments.inquiry_id;

UPDATE notifications
SET target_url = '/portal/sales-supervisor/inquiries'
WHERE source_type = 'inquiries'
  AND target_url = '/portal/team-leader/inquiries';
