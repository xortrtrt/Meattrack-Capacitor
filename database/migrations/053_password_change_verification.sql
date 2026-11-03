CREATE TABLE IF NOT EXISTS account_password_change_challenges (
    challenge_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON DELETE CASCADE,
    otp_hash text NOT NULL,
    original_password_hash text NOT NULL,
    grant_hash text,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 5),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL DEFAULT now() + interval '10 minutes',
    verified_at timestamptz,
    consumed_at timestamptz
);
CREATE INDEX IF NOT EXISTS ix_password_change_challenges_account
    ON account_password_change_challenges(account_id, created_at DESC);
