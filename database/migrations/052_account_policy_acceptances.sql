CREATE TABLE IF NOT EXISTS account_policy_acceptances (
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    policy_type text NOT NULL CHECK (policy_type IN ('privacy_notice', 'terms')),
    policy_version text NOT NULL CHECK (btrim(policy_version) <> ''),
    acceptance_source text NOT NULL CHECK (btrim(acceptance_source) <> ''),
    accepted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, policy_type, policy_version)
);
