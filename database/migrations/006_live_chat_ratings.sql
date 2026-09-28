CREATE TABLE IF NOT EXISTS chat_service_ratings (
    conversation_id uuid PRIMARY KEY
        REFERENCES chat_conversations(conversation_id) ON UPDATE CASCADE ON DELETE CASCADE,
    team_leader_account_id bigint NOT NULL
        REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    rating smallint NOT NULL CHECK (rating BETWEEN 1 AND 5),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_chat_service_ratings_leader_created
    ON chat_service_ratings (team_leader_account_id, created_at DESC);
