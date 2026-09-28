BEGIN;

ALTER TABLE inquiries
    ADD COLUMN IF NOT EXISTS rejection_reason text;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_inquiries_rejection_reason_length'
    ) THEN
        ALTER TABLE inquiries
            ADD CONSTRAINT ck_inquiries_rejection_reason_length
            CHECK (rejection_reason IS NULL OR char_length(btrim(rejection_reason)) BETWEEN 5 AND 1000);
    END IF;
END $$;

COMMIT;
