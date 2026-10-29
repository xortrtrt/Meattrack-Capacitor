-- A resolved follow-up must retain the required customer-facing explanation.
-- CHECK expressions otherwise permit NULL through SQL's three-valued logic.
ALTER TABLE return_customer_followups DROP CONSTRAINT IF EXISTS return_followup_resolution_required;
ALTER TABLE return_customer_followups ADD CONSTRAINT return_followup_resolution_required
CHECK(status<>'resolved' OR resolution_message IS NOT NULL);
