"""Owner coverage predicates shared by exception counts and filtered directories."""

INQUIRY_COVERAGE = """(i.status IN ('pending','assigned','contacted') AND NOT EXISTS(
 SELECT 1 FROM accounts a WHERE a.account_id=i.assigned_team_leader_account_id
 AND a.is_active AND a.team_leader_role IN ('sales','sales-supervisor')))"""

RESELLER_COVERAGE = """NOT EXISTS(SELECT 1 FROM accounts a
 WHERE a.account_id=r.team_leader_account_id AND a.is_active
 AND a.team_leader_role IN ('sales','sales-supervisor'))"""

REFUND_COVERAGE = """(r.status IN ('pending','processing','failed') AND NOT EXISTS(
 SELECT 1 FROM accounts a WHERE a.account_id=r.assigned_supervisor_account_id
 AND a.is_active AND a.team_leader_role='sales-supervisor'))"""
