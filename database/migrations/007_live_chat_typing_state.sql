ALTER TABLE chat_conversations
    ADD COLUMN IF NOT EXISTS visitor_typing_until timestamptz,
    ADD COLUMN IF NOT EXISTS leader_typing_until timestamptz;
