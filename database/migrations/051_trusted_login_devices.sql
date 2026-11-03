CREATE TABLE IF NOT EXISTS trusted_login_devices (
    account_id bigint NOT NULL REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    device_token_hash char(64) NOT NULL,
    trusted_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (account_id, device_token_hash),
    CHECK (length(device_token_hash) = 64)
);

CREATE INDEX IF NOT EXISTS ix_trusted_login_devices_expiry
    ON trusted_login_devices (expires_at);
