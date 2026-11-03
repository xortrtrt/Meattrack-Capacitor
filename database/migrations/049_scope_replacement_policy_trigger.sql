-- Legacy rows do not participate in replacement-first validation. Avoid queuing
-- unnecessary deferred events that prevent historical reconciliation plus DDL.
DROP TRIGGER IF EXISTS validate_replacement_first_policy ON product_returns;
CREATE CONSTRAINT TRIGGER validate_replacement_first_policy
AFTER INSERT OR UPDATE ON product_returns
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
WHEN (NEW.return_policy_version = 2)
EXECUTE FUNCTION validate_replacement_first_policy();
