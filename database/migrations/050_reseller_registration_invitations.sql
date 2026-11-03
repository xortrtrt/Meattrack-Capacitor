ALTER TABLE account_login_otps
    ADD COLUMN IF NOT EXISTS purpose text NOT NULL DEFAULT 'login';
ALTER TABLE account_login_otps
    ADD COLUMN IF NOT EXISTS attempt_count integer NOT NULL DEFAULT 0;

ALTER TABLE account_login_otps DROP CONSTRAINT IF EXISTS account_login_otps_purpose_check;
ALTER TABLE account_login_otps ADD CONSTRAINT account_login_otps_purpose_check
    CHECK (purpose IN ('login', 'reseller_registration'));
ALTER TABLE account_login_otps DROP CONSTRAINT IF EXISTS account_login_otps_attempt_count_check;
ALTER TABLE account_login_otps ADD CONSTRAINT account_login_otps_attempt_count_check
    CHECK (attempt_count BETWEEN 0 AND 5);

CREATE TABLE IF NOT EXISTS reseller_registration_invitations (
    invitation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    invited_by_account_id bigint NOT NULL REFERENCES accounts(account_id),
    invited_email text NOT NULL,
    token_hash char(64) NOT NULL UNIQUE,
    expires_at timestamptz NOT NULL,
    registration_account_id bigint UNIQUE REFERENCES accounts(account_id) ON DELETE SET NULL,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(invited_email) <> ''),
    CHECK (length(token_hash) = 64)
);

CREATE INDEX IF NOT EXISTS ix_reseller_registration_invitations_email
    ON reseller_registration_invitations (lower(invited_email), created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS ux_reseller_registration_invitation_open_email
    ON reseller_registration_invitations (lower(invited_email))
    WHERE consumed_at IS NULL AND registration_account_id IS NULL;
