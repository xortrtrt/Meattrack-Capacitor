CREATE TABLE IF NOT EXISTS reseller_conversations (
    conversation_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reseller_account_id bigint NOT NULL
        REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    team_leader_account_id bigint NOT NULL
        REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    last_message_at timestamptz,
    reseller_deleted_at timestamptz,
    team_leader_deleted_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (reseller_account_id, team_leader_account_id)
);

CREATE TABLE IF NOT EXISTS reseller_messages (
    reseller_message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id bigint NOT NULL
        REFERENCES reseller_conversations(conversation_id) ON UPDATE CASCADE ON DELETE CASCADE,
    client_message_id uuid NOT NULL,
    sender_account_id bigint NOT NULL
        REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    content text NOT NULL CHECK (char_length(btrim(content)) BETWEEN 1 AND 500),
    read_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, client_message_id)
);

CREATE INDEX IF NOT EXISTS ix_reseller_conversations_leader_activity
    ON reseller_conversations (team_leader_account_id, last_message_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS ix_reseller_messages_conversation
    ON reseller_messages (conversation_id, reseller_message_id);
CREATE INDEX IF NOT EXISTS ix_reseller_messages_unread
    ON reseller_messages (conversation_id, sender_account_id)
    WHERE read_at IS NULL;
