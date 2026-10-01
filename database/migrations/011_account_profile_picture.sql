ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS profile_image_path text;

ALTER TABLE accounts
    ADD COLUMN IF NOT EXISTS profile_image_position_x numeric(5,2) NOT NULL DEFAULT 50,
    ADD COLUMN IF NOT EXISTS profile_image_position_y numeric(5,2) NOT NULL DEFAULT 50,
    ADD COLUMN IF NOT EXISTS profile_image_scale numeric(4,2) NOT NULL DEFAULT 1;
