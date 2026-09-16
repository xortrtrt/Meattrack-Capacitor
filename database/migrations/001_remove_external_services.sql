DROP INDEX IF EXISTS ux_accounts_auth_user_id;

ALTER TABLE IF EXISTS accounts
    DROP COLUMN IF EXISTS auth_user_id,
    DROP COLUMN IF EXISTS auth_provider;

DROP TABLE IF EXISTS media_assets;
