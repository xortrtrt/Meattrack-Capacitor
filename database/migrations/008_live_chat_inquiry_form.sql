BEGIN;

ALTER TABLE chat_conversations
    ADD COLUMN IF NOT EXISTS inquiry_form_requested_at timestamptz;

COMMIT;
