from __future__ import annotations

import hashlib
import mimetypes
import sys
from pathlib import Path

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DATABASE_URL

STATIC_IMG_DIR = PROJECT_ROOT / "app" / "static" / "img"

CREATE_MEDIA_ASSETS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS media_assets (
    media_asset_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename text NOT NULL UNIQUE,
    content_type text NOT NULL,
    content bytea NOT NULL,
    size_bytes integer NOT NULL CHECK (size_bytes >= 0),
    checksum_sha256 text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (btrim(filename) <> ''),
    CHECK (filename !~ '[\\\\/]'),
    CHECK (btrim(content_type) <> ''),
    CHECK (length(checksum_sha256) = 64)
);
"""


def iter_image_files() -> list[Path]:
    return sorted(path for path in STATIC_IMG_DIR.iterdir() if path.is_file())


def import_images() -> int:
    if not STATIC_IMG_DIR.exists():
        raise RuntimeError(f"Image directory does not exist: {STATIC_IMG_DIR}")

    image_paths = iter_image_files()
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_MEDIA_ASSETS_TABLE_SQL)
            for path in image_paths:
                content = path.read_bytes()
                content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
                checksum = hashlib.sha256(content).hexdigest()
                cur.execute(
                    """
                    INSERT INTO media_assets (
                        filename, content_type, content, size_bytes, checksum_sha256
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (filename) DO UPDATE SET
                        content_type = EXCLUDED.content_type,
                        content = EXCLUDED.content,
                        size_bytes = EXCLUDED.size_bytes,
                        checksum_sha256 = EXCLUDED.checksum_sha256,
                        updated_at = now();
                    """,
                    (
                        path.name,
                        content_type,
                        psycopg2.Binary(content),
                        len(content),
                        checksum,
                    ),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return len(image_paths)


def main() -> None:
    count = import_images()
    print(f"Imported {count} image assets into media_assets.")


if __name__ == "__main__":
    main()
