from datetime import date, timedelta
from pathlib import Path
import sys

import psycopg2


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config import DATABASE_URL, OWNER_PASSWORD, RESELLER_PASSWORD, TEAM_LEADER_PASSWORD
from app.security import hash_password

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


def import_static_images(cur):
    import hashlib
    import mimetypes

    cur.execute(CREATE_MEDIA_ASSETS_TABLE_SQL)
    for path in sorted(file_path for file_path in STATIC_IMG_DIR.iterdir() if file_path.is_file()):
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
            (path.name, content_type, psycopg2.Binary(content), len(content), checksum),
        )

def main():
    dsn = DATABASE_URL
    print("Connecting to database...")
    conn = psycopg2.connect(dsn)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Resetting public schema...")
        cur.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.commit()
        
        print("Reading database/schema.sql...")
        with open("database/schema.sql", "r", encoding="utf-8") as f:
            schema_sql = f.read()
            
        print("Applying database schema...")
        cur.execute(schema_sql)
        conn.commit()
        print("Schema applied successfully.")
        
        today = date.today()
        # 1. Departments
        print("Seeding departments...")
        cur.execute("""
            INSERT INTO departments (department_name, description) VALUES
            ('Production', 'Meat processing and packaging department.'),
            ('Retail Floor', 'Front of house and retail checkout area.'),
            ('Cold Storage', 'Main cold chain storage and freezer area.'),
            ('Administration', 'Management and administrative support.')
            RETURNING department_id, department_name;
        """)
        departments = {name: dep_id for dep_id, name in cur.fetchall()}
        
        # 2. Resellers
        print("Seeding resellers...")
        cur.execute("""
            INSERT INTO resellers (business_name, contact_person, email, contact_number, address, reseller_status, created_at) VALUES
            ('Lipa Fresh Mart', 'Carlo Mendoza', 'reseller@lipafresh.test', '0917 204 1198', 'Poblacion, Lipa City', 'active', %s)
            RETURNING reseller_id, business_name;
        """, (today - timedelta(days=30),))
        resellers = {name: res_id for res_id, name in cur.fetchall()}
        
        # 3. Accounts
        print("Seeding accounts...")
        # Owner account
        cur.execute("""
            INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, is_active)
            VALUES ('owner', NULL, 'Patric Mapa', 'patric.mapa@gmail.com', %s, true)
            RETURNING account_id;
        """, (hash_password(OWNER_PASSWORD),))
        cur.fetchone()
        
        # Team Leader (Maria Santos)
        cur.execute("""
            INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, is_active)
            VALUES ('team_leader', NULL, 'Maria Santos', 'leader@batangaspremium.test', %s, true)
            RETURNING account_id;
        """, (hash_password(TEAM_LEADER_PASSWORD),))
        leader_account_id = cur.fetchone()[0]
        
        # Reseller (Lipa Fresh Mart)
        cur.execute("""
            INSERT INTO accounts (account_type, reseller_id, name, email, password_hash, is_active)
            VALUES ('reseller', %s, 'Lipa Fresh Mart', 'reseller@lipafresh.test', %s, true)
            RETURNING account_id;
        """, (resellers['Lipa Fresh Mart'], hash_password(RESELLER_PASSWORD)))
        cur.fetchone()
        
        # Update resellers approved_by
        cur.execute("""
            UPDATE resellers SET approved_by_account_id = %s, approved_at = %s WHERE reseller_id = %s;
        """, (leader_account_id, today - timedelta(days=30), resellers['Lipa Fresh Mart']))
        
        # 4. Inventory Items
        print("Seeding raw material inventory items...")
        cur.execute("""
            INSERT INTO inventory_items (item_type, name, unit, quantity_available, category) VALUES
            ('raw_material', 'Chicken', 'kg', 60.0, 'Meat'),
            ('raw_material', 'Pork', 'kg', 80.0, 'Meat'),
            ('raw_material', 'Beef', 'kg', 50.0, 'Meat')
            RETURNING item_id, name;
        """)
        raw_materials = {name: item_id for item_id, name in cur.fetchall()}

        # 5. Finished Products
        print("Seeding finished product inventory items...")
        cur.execute("""
            INSERT INTO inventory_items (item_type, name, description, unit, base_price, category, is_active) VALUES
            ('finished_product', 'Pork Garlic Longganisa', 'Batangas Premium frozen garlic longganisa pack.', 'pack', 120.00, 'Pork', true),
            ('finished_product', 'Tocino Ala Eh', 'Sweet cured frozen pork tocino pack.', 'pack', 130.00, 'Pork', true),
            ('finished_product', 'Beef Tapa Ala Eh', 'Savory marinated premium frozen beef tapa pack.', 'pack', 180.00, 'Beef', true),
            ('finished_product', 'Cheesy Overload Sausage', 'Ready-to-cook frozen cheese sausage pack.', 'pack', 150.00, 'Pork', true),
            ('finished_product', 'Hungarian Sausage', 'Savory Hungarian sausage pack.', 'pack', 160.00, 'Beef', true),
            ('finished_product', 'Bacon (Smoked)', 'Smoked pork bacon strips.', 'pack', 170.00, 'Pork', true)
            RETURNING item_id, name;
        """)
        products = {name: item_id for item_id, name in cur.fetchall()}

        # 6. Product Recipes
        print("Seeding product recipes...")
        cur.execute("""
            INSERT INTO product_recipes (product_item_id, material_item_id, quantity_required, unit) VALUES
            (%s, %s, 0.500, 'kg'),
            (%s, %s, 0.500, 'kg'),
            (%s, %s, 0.500, 'kg'),
            (%s, %s, 0.450, 'kg'),
            (%s, %s, 0.500, 'kg'),
            (%s, %s, 0.450, 'kg');
        """, (
            products['Pork Garlic Longganisa'], raw_materials['Pork'],
            products['Tocino Ala Eh'], raw_materials['Pork'],
            products['Beef Tapa Ala Eh'], raw_materials['Beef'],
            products['Cheesy Overload Sausage'], raw_materials['Pork'],
            products['Hungarian Sausage'], raw_materials['Beef'],
            products['Bacon (Smoked)'], raw_materials['Pork']
        ))

        # 7. Initial Product Batches
        print("Seeding initial finished product batches...")
        cur.execute("""
            INSERT INTO inventory_batches (item_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status) VALUES
            (%s, 'PGL-INITIAL', 'direct_received', 100, 80, 'pack', %s, %s, 'approved'),
            (%s, 'TAE-INITIAL', 'direct_received', 80, 65, 'pack', %s, %s, 'approved'),
            (%s, 'BTA-INITIAL', 'direct_received', 50, 45, 'pack', %s, %s, 'approved')
            RETURNING batch_id, batch_code;
        """, (
            products['Pork Garlic Longganisa'], today - timedelta(days=2), today + timedelta(days=15),
            products['Tocino Ala Eh'], today - timedelta(days=2), today + timedelta(days=20),
            products['Beef Tapa Ala Eh'], today - timedelta(days=2), today + timedelta(days=10)
        ))

        print("Importing static image assets into media_assets...")
        import_static_images(cur)
        
        # Commit everything
        conn.commit()
        print("Database baseline seeding completed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"Error during seeding: {e}")
        raise e
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
