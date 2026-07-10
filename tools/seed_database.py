import psycopg2
from datetime import date, timedelta

from app.config import DATABASE_URL, OWNER_PASSWORD, RESELLER_PASSWORD, TEAM_LEADER_PASSWORD
from app.security import hash_password

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
        
        # 2. Employees
        print("Seeding employees...")
        cur.execute("""
            INSERT INTO employees (department_id, name, position, employment_status, is_department_leader) VALUES
            (%s, 'Maria Santos', 'Team Leader', 'active', true),
            (%s, 'Benjie Cruz', 'Sales Staff', 'active', false),
            (%s, 'Alma Reyes', 'Stock Handler', 'active', false)
            RETURNING employee_id, name;
        """, (departments['Retail Floor'], departments['Retail Floor'], departments['Cold Storage']))
        employees = {name: emp_id for emp_id, name in cur.fetchall()}
        
        # 3. Resellers
        print("Seeding resellers...")
        cur.execute("""
            INSERT INTO resellers (business_name, contact_person, email, contact_number, address, reseller_status, created_at) VALUES
            ('Lipa Fresh Mart', 'Carlo Mendoza', 'reseller@lipafresh.test', '0917 204 1198', 'Poblacion, Lipa City', 'active', %s)
            RETURNING reseller_id, business_name;
        """, (today - timedelta(days=30),))
        resellers = {name: res_id for res_id, name in cur.fetchall()}
        
        # 4. Accounts
        print("Seeding accounts...")
        # Owner account
        cur.execute("""
            INSERT INTO accounts (account_type, employee_id, reseller_id, name, email, password_hash, is_active)
            VALUES ('owner', NULL, NULL, 'Patric Mapa', 'patric.mapa@gmail.com', %s, true)
            RETURNING account_id;
        """, (hash_password(OWNER_PASSWORD),))
        cur.fetchone()
        
        # Team Leader (Maria Santos)
        cur.execute("""
            INSERT INTO accounts (account_type, employee_id, reseller_id, name, email, password_hash, is_active)
            VALUES ('team_leader', %s, NULL, 'Maria Santos', 'leader@batangaspremium.test', %s, true)
            RETURNING account_id;
        """, (employees['Maria Santos'], hash_password(TEAM_LEADER_PASSWORD)))
        leader_account_id = cur.fetchone()[0]
        
        # Reseller (Lipa Fresh Mart)
        cur.execute("""
            INSERT INTO accounts (account_type, employee_id, reseller_id, name, email, password_hash, is_active)
            VALUES ('reseller', NULL, %s, 'Lipa Fresh Mart', 'reseller@lipafresh.test', %s, true)
            RETURNING account_id;
        """, (resellers['Lipa Fresh Mart'], hash_password(RESELLER_PASSWORD)))
        cur.fetchone()
        
        # Update resellers approved_by
        cur.execute("""
            UPDATE resellers SET approved_by_account_id = %s, approved_at = %s WHERE reseller_id = %s;
        """, (leader_account_id, today - timedelta(days=30), resellers['Lipa Fresh Mart']))
        
        # 5. Raw Materials
        print("Seeding raw materials...")
        cur.execute("""
            INSERT INTO raw_materials (name, unit, reorder_level, category) VALUES
            ('Pork Shoulder', 'kg', 20.0, 'Meat'),
            ('Beef Sirloin', 'kg', 10.0, 'Meat'),
            ('Chicken Fillet', 'kg', 10.0, 'Meat'),
            ('Garlic & Seasoning Blend', 'kg', 3.0, 'Seasoning'),
            ('Curing Mix', 'kg', 2.0, 'Seasoning'),
            ('Cheese Blend', 'kg', 3.0, 'Ingredient'),
            ('Pack Wrapper', 'pc', 100.0, 'Packaging')
            RETURNING raw_material_id, name;
        """)
        raw_materials = {name: material_id for material_id, name in cur.fetchall()}

        print("Seeding raw material batches...")
        cur.execute("""
            INSERT INTO raw_material_batches (raw_material_id, batch_code, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status) VALUES
            (%s, 'PORK-INITIAL', 80, 80, 'kg', %s, %s, 'approved'),
            (%s, 'BEEF-INITIAL', 50, 50, 'kg', %s, %s, 'approved'),
            (%s, 'CHICKEN-INITIAL', 40, 40, 'kg', %s, %s, 'approved'),
            (%s, 'GARLIC-SEASONING-INITIAL', 12, 12, 'kg', %s, %s, 'approved'),
            (%s, 'CURING-MIX-INITIAL', 8, 8, 'kg', %s, %s, 'approved'),
            (%s, 'CHEESE-BLEND-INITIAL', 10, 10, 'kg', %s, %s, 'approved'),
            (%s, 'WRAPPER-INITIAL', 600, 600, 'pc', %s, %s, 'approved');
        """, (
            raw_materials['Pork Shoulder'], today - timedelta(days=2), today + timedelta(days=20),
            raw_materials['Beef Sirloin'], today - timedelta(days=2), today + timedelta(days=20),
            raw_materials['Chicken Fillet'], today - timedelta(days=2), today + timedelta(days=18),
            raw_materials['Garlic & Seasoning Blend'], today - timedelta(days=2), today + timedelta(days=60),
            raw_materials['Curing Mix'], today - timedelta(days=2), today + timedelta(days=60),
            raw_materials['Cheese Blend'], today - timedelta(days=2), today + timedelta(days=30),
            raw_materials['Pack Wrapper'], today - timedelta(days=2), today + timedelta(days=365)
        ))

        # 6. Products
        print("Seeding products...")
        cur.execute("""
            INSERT INTO products (name, description, unit, base_price, reorder_level, category, is_active) VALUES
            ('Pork Garlic Longganisa', 'Batangas Premium frozen garlic longganisa pack.', 'pack', 120.00, 20.0, 'Pork', true),
            ('Tocino Ala Eh', 'Sweet cured frozen pork tocino pack.', 'pack', 130.00, 15.0, 'Pork', true),
            ('Beef Tapa Ala Eh', 'Savory marinated premium frozen beef tapa pack.', 'pack', 180.00, 10.0, 'Beef', true),
            ('Cheesy Overload Sausage', 'Ready-to-cook frozen cheese sausage pack.', 'pack', 150.00, 10.0, 'Pork', true),
            ('Hungarian Sausage', 'Savory Hungarian sausage pack.', 'pack', 160.00, 10.0, 'Beef', true),
            ('Bacon (Smoked)', 'Smoked pork bacon strips.', 'pack', 170.00, 10.0, 'Pork', true)
            RETURNING product_id, name;
        """)
        products = {name: prod_id for prod_id, name in cur.fetchall()}

        # 7. Product Recipes
        print("Seeding product recipes...")
        cur.execute("""
            INSERT INTO product_recipes (product_id, raw_material_id, quantity_required, unit) VALUES
            (%s, %s, 0.500, 'kg'),
            (%s, %s, 0.020, 'kg'),
            (%s, %s, 1.000, 'pc'),
            (%s, %s, 0.500, 'kg'),
            (%s, %s, 0.020, 'kg'),
            (%s, %s, 1.000, 'pc'),
            (%s, %s, 0.500, 'kg'),
            (%s, %s, 0.010, 'kg'),
            (%s, %s, 1.000, 'pc'),
            (%s, %s, 0.450, 'kg'),
            (%s, %s, 0.050, 'kg'),
            (%s, %s, 1.000, 'pc'),
            (%s, %s, 0.500, 'kg'),
            (%s, %s, 0.012, 'kg'),
            (%s, %s, 1.000, 'pc'),
            (%s, %s, 0.450, 'kg'),
            (%s, %s, 0.010, 'kg'),
            (%s, %s, 1.000, 'pc');
        """, (
            products['Pork Garlic Longganisa'], raw_materials['Pork Shoulder'],
            products['Pork Garlic Longganisa'], raw_materials['Garlic & Seasoning Blend'],
            products['Pork Garlic Longganisa'], raw_materials['Pack Wrapper'],
            products['Tocino Ala Eh'], raw_materials['Pork Shoulder'],
            products['Tocino Ala Eh'], raw_materials['Curing Mix'],
            products['Tocino Ala Eh'], raw_materials['Pack Wrapper'],
            products['Beef Tapa Ala Eh'], raw_materials['Beef Sirloin'],
            products['Beef Tapa Ala Eh'], raw_materials['Garlic & Seasoning Blend'],
            products['Beef Tapa Ala Eh'], raw_materials['Pack Wrapper'],
            products['Cheesy Overload Sausage'], raw_materials['Pork Shoulder'],
            products['Cheesy Overload Sausage'], raw_materials['Cheese Blend'],
            products['Cheesy Overload Sausage'], raw_materials['Pack Wrapper'],
            products['Hungarian Sausage'], raw_materials['Beef Sirloin'],
            products['Hungarian Sausage'], raw_materials['Garlic & Seasoning Blend'],
            products['Hungarian Sausage'], raw_materials['Pack Wrapper'],
            products['Bacon (Smoked)'], raw_materials['Pork Shoulder'],
            products['Bacon (Smoked)'], raw_materials['Curing Mix'],
            products['Bacon (Smoked)'], raw_materials['Pack Wrapper']
        ))

        # 8. Initial Product Batches
        print("Seeding initial product batches...")
        cur.execute("""
            INSERT INTO product_batches (product_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status) VALUES
            (%s, 'PGL-INITIAL', 'direct_received', 100, 80, 'pack', %s, %s, 'approved'),
            (%s, 'TAE-INITIAL', 'direct_received', 80, 65, 'pack', %s, %s, 'approved'),
            (%s, 'BTA-INITIAL', 'direct_received', 50, 45, 'pack', %s, %s, 'approved')
            RETURNING product_batch_id, batch_code;
        """, (
            products['Pork Garlic Longganisa'], today - timedelta(days=2), today + timedelta(days=15),
            products['Tocino Ala Eh'], today - timedelta(days=2), today + timedelta(days=20),
            products['Beef Tapa Ala Eh'], today - timedelta(days=2), today + timedelta(days=10)
        ))
        
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
