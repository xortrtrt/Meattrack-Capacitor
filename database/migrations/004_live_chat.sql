CREATE TABLE IF NOT EXISTS chat_conversations (
    conversation_id uuid PRIMARY KEY,
    display_name text NOT NULL CHECK (char_length(btrim(display_name)) BETWEEN 2 AND 80),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'active', 'awaiting_contact', 'follow_up', 'closed', 'cancelled')),
    escalation_reason text NOT NULL DEFAULT 'requested',
    assigned_team_leader_account_id bigint
        REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    fallback_contact_type text CHECK (fallback_contact_type IS NULL OR fallback_contact_type IN ('email', 'phone')),
    fallback_contact text,
    privacy_consent_at timestamptz NOT NULL DEFAULT now(),
    queued_at timestamptz NOT NULL DEFAULT now(),
    claimed_at timestamptz,
    last_visitor_at timestamptz,
    last_leader_at timestamptz,
    closed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK ((fallback_contact_type IS NULL) = (fallback_contact IS NULL))
);

CREATE TABLE IF NOT EXISTS chat_messages (
    chat_message_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id uuid NOT NULL REFERENCES chat_conversations(conversation_id) ON UPDATE CASCADE ON DELETE CASCADE,
    client_message_id uuid NOT NULL,
    sender_type text NOT NULL CHECK (sender_type IN ('visitor', 'team_leader', 'system')),
    sender_account_id bigint REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE SET NULL,
    content text NOT NULL CHECK (char_length(btrim(content)) BETWEEN 1 AND 500),
    publication_status text NOT NULL DEFAULT 'pending'
        CHECK (publication_status IN ('pending', 'published', 'failed')),
    publish_attempts integer NOT NULL DEFAULT 0 CHECK (publish_attempts >= 0),
    last_publish_error text,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (conversation_id, client_message_id)
);

CREATE TABLE IF NOT EXISTS team_leader_presence (
    account_id bigint PRIMARY KEY REFERENCES accounts(account_id) ON UPDATE CASCADE ON DELETE CASCADE,
    availability text NOT NULL DEFAULT 'offline' CHECK (availability IN ('available', 'busy', 'offline')),
    active_conversation_id uuid REFERENCES chat_conversations(conversation_id) ON UPDATE CASCADE ON DELETE SET NULL,
    heartbeat_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS rate_limit_buckets (
    event_type text NOT NULL,
    subject_hash text NOT NULL,
    window_seconds integer NOT NULL CHECK (window_seconds > 0),
    bucket_started_at timestamptz NOT NULL,
    request_count integer NOT NULL DEFAULT 1 CHECK (request_count > 0),
    expires_at timestamptz NOT NULL,
    PRIMARY KEY (event_type, subject_hash, window_seconds, bucket_started_at)
);

ALTER TABLE inquiries ADD COLUMN IF NOT EXISTS chat_conversation_id uuid;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fk_inquiries_chat_conversation'
    ) THEN
        ALTER TABLE inquiries
            ADD CONSTRAINT fk_inquiries_chat_conversation
            FOREIGN KEY (chat_conversation_id)
            REFERENCES chat_conversations(conversation_id)
            ON UPDATE CASCADE ON DELETE SET NULL;
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_inquiries_chat_conversation
    ON inquiries (chat_conversation_id) WHERE chat_conversation_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_chat_conversations_queue
    ON chat_conversations (status, queued_at);
CREATE INDEX IF NOT EXISTS ix_chat_conversations_leader_status
    ON chat_conversations (assigned_team_leader_account_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS ix_chat_messages_conversation_id
    ON chat_messages (conversation_id, chat_message_id);
CREATE INDEX IF NOT EXISTS ix_chat_messages_pending
    ON chat_messages (publication_status, created_at) WHERE publication_status <> 'published';
CREATE INDEX IF NOT EXISTS ix_team_leader_presence_available
    ON team_leader_presence (availability, heartbeat_at DESC);
CREATE INDEX IF NOT EXISTS ix_rate_limit_buckets_expiry
    ON rate_limit_buckets (expires_at);
