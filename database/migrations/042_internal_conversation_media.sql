ALTER TABLE reseller_messages DROP CONSTRAINT IF EXISTS reseller_messages_content_check;
ALTER TABLE reseller_messages ADD CONSTRAINT reseller_messages_content_check CHECK(char_length(content)<=500);
ALTER TABLE reseller_messages ADD COLUMN IF NOT EXISTS attachment_count integer NOT NULL DEFAULT 0 CHECK(attachment_count BETWEEN 0 AND 3);
ALTER TABLE reseller_messages DROP CONSTRAINT IF EXISTS reseller_message_not_empty;
ALTER TABLE reseller_messages ADD CONSTRAINT reseller_message_not_empty CHECK(char_length(btrim(content))>0 OR attachment_count>0);
CREATE TABLE IF NOT EXISTS reseller_message_attachments (
    attachment_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    reseller_message_id bigint NOT NULL REFERENCES reseller_messages ON DELETE RESTRICT,
    filename text NOT NULL CHECK(filename<>'' AND filename !~ '[\\/]'),
    content_type text NOT NULL CHECK(content_type IN ('image/jpeg','image/png','image/webp','video/mp4','video/webm')),
    content bytea NOT NULL,
    size_bytes integer NOT NULL CHECK(size_bytes>0 AND size_bytes<=26214400),
    checksum_sha256 char(64) NOT NULL,
    CHECK(octet_length(content)=size_bytes),
    CHECK(content_type LIKE 'video/%' OR size_bytes<=5242880)
);
CREATE INDEX IF NOT EXISTS ix_reseller_message_attachments_message ON reseller_message_attachments(reseller_message_id,attachment_id);
DROP TRIGGER IF EXISTS immutable_reseller_message_attachments ON reseller_message_attachments;
CREATE TRIGGER immutable_reseller_message_attachments BEFORE UPDATE OR DELETE OR TRUNCATE ON reseller_message_attachments
FOR EACH STATEMENT EXECUTE FUNCTION protect_refund_trace_records();
CREATE OR REPLACE FUNCTION validate_message_attachments() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE mid bigint; expected integer; actual integer; bytes bigint;
BEGIN
    mid:=NEW.reseller_message_id;
    SELECT attachment_count INTO expected FROM reseller_messages WHERE reseller_message_id=mid;
    SELECT count(*),COALESCE(sum(size_bytes),0) INTO actual,bytes FROM reseller_message_attachments WHERE reseller_message_id=mid;
    IF actual<>expected OR bytes>31457280 THEN RAISE EXCEPTION 'Message attachments do not match the declared count or exceed 30 MB.'; END IF;
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS validate_reseller_message_attachments ON reseller_messages;
CREATE CONSTRAINT TRIGGER validate_reseller_message_attachments AFTER INSERT ON reseller_messages
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_message_attachments();
DROP TRIGGER IF EXISTS validate_reseller_attachment_count ON reseller_message_attachments;
CREATE CONSTRAINT TRIGGER validate_reseller_attachment_count AFTER INSERT ON reseller_message_attachments
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION validate_message_attachments();

CREATE TABLE IF NOT EXISTS refund_message_reads (
    order_refund_id bigint NOT NULL REFERENCES order_refunds ON DELETE RESTRICT,
    account_id bigint NOT NULL REFERENCES accounts ON DELETE RESTRICT,
    last_message_id bigint NOT NULL DEFAULT 0 CHECK(last_message_id>=0),
    read_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(order_refund_id,account_id)
);
ALTER TABLE order_refund_messages ADD COLUMN IF NOT EXISTS message_kind text NOT NULL DEFAULT 'text' CHECK(message_kind IN ('text','system'));
ALTER TABLE order_refund_messages ADD COLUMN IF NOT EXISTS system_event_key text UNIQUE;
CREATE INDEX IF NOT EXISTS ix_reseller_messages_unread_cursor ON reseller_messages(conversation_id,sender_account_id,reseller_message_id) WHERE read_at IS NULL;

ALTER TABLE order_refund_attempts ADD COLUMN IF NOT EXISTS transfer_amount numeric(12,2);
ALTER TABLE order_refund_attempts ADD COLUMN IF NOT EXISTS receipt_extraction_method text;
ALTER TABLE order_refund_attempts ADD COLUMN IF NOT EXISTS receipt_ocr_confidence numeric;
ALTER TABLE order_refund_attempts ADD COLUMN IF NOT EXISTS receipt_ocr_engine text;
CREATE OR REPLACE FUNCTION validate_refund_transfer_amount() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status='completed' AND (TG_OP='INSERT' OR OLD.status<>'completed') AND (
        NEW.transfer_amount IS NULL OR NEW.transfer_amount IS DISTINCT FROM
        (SELECT amount FROM order_refunds WHERE order_refund_id=NEW.order_refund_id)) THEN
        RAISE EXCEPTION 'Confirmed transfer amount must equal the refund due.';
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS validate_refund_transfer_amount ON order_refund_attempts;
CREATE TRIGGER validate_refund_transfer_amount BEFORE INSERT OR UPDATE ON order_refund_attempts
FOR EACH ROW EXECUTE FUNCTION validate_refund_transfer_amount();
