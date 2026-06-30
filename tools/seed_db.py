import psycopg2
from datetime import date, datetime, timedelta

def main():
    dsn = "postgresql://meattrack:meattrack@127.0.0.1:5433/meattrack"
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
        
        # Now seed data!
        today = date.today()
        now_dt = datetime.now()
        
        # 1. Departments
        print("Seeding departments...")
        cur.execute("""
            INSERT INTO departments (department_name, description) VALUES
            ('Retail Floor', 'Front of house and retail checkout area.'),
            ('Cold Storage', 'Main cold chain storage and freezer area.')
            RETURNING department_id, department_name;
        """)
        departments = {name: dep_id for dep_id, name in cur.fetchall()}
        
        # 2. Employees
        print("Seeding employees...")
        cur.execute("""
            INSERT INTO employees (department_id, name, position, employment_status) VALUES
            (%s, 'Maria Santos', 'Team Leader', 'active'),
            (%s, 'Benjie Cruz', 'Sales Staff', 'active'),
            (%s, 'Alma Reyes', 'Stock Handler', 'active'),
            (%s, 'Jonel Ramos', 'Stock Leader', 'active')
            RETURNING employee_id, name;
        """, (departments['Retail Floor'], departments['Retail Floor'], departments['Cold Storage'], departments['Cold Storage']))
        employees = {name: emp_id for emp_id, name in cur.fetchall()}
        
        # 3. Resellers
        print("Seeding resellers...")
        cur.execute("""
            INSERT INTO resellers (business_name, contact_person, email, contact_number, address, reseller_status, created_at) VALUES
            ('Lipa Fresh Mart', 'Carlo Mendoza', 'reseller@lipafresh.test', '0917 204 1198', 'Poblacion, Lipa City', 'active', %s),
            ('Taal Kitchen Supply', 'Rhea Villanueva', 'orders@taalkitchen.test', '0918 442 9033', 'Taal, Batangas', 'active', %s),
            ('San Jose Meat Corner', 'Arnel Dimaapi', 'sjmeatcorner@test.local', '0920 774 5521', 'San Jose, Batangas', 'active', %s)
            RETURNING reseller_id, business_name;
        """, (today - timedelta(days=68), today - timedelta(days=42), today - timedelta(days=19)))
        resellers = {name: res_id for res_id, name in cur.fetchall()}
        
        # 4. Accounts
        print("Seeding accounts...")
        # Owner
        cur.execute("""
            INSERT INTO accounts (account_type, employee_id, reseller_id, name, email, password_hash, is_active)
            VALUES ('owner', NULL, NULL, 'Patricia Manalo', 'owner@batangaspremium.test', 'demo1234', true)
            RETURNING account_id;
        """)
        owner_account_id = cur.fetchone()[0]
        
        # Team Leader (Maria Santos)
        cur.execute("""
            INSERT INTO accounts (account_type, employee_id, reseller_id, name, email, password_hash, is_active)
            VALUES ('team_leader', %s, NULL, 'Maria Santos', 'leader@batangaspremium.test', 'demo1234', true)
            RETURNING account_id;
        """, (employees['Maria Santos'],))
        leader_account_id = cur.fetchone()[0]
        
        # Reseller accounts
        cur.execute("""
            INSERT INTO accounts (account_type, employee_id, reseller_id, name, email, password_hash, is_active) VALUES
            ('reseller', NULL, %s, 'Lipa Fresh Mart', 'reseller@lipafresh.test', 'demo1234', true),
            ('reseller', NULL, %s, 'Taal Kitchen Supply', 'orders@taalkitchen.test', 'demo1234', true),
            ('reseller', NULL, %s, 'San Jose Meat Corner', 'sjmeatcorner@test.local', 'demo1234', true)
            RETURNING account_id, email;
        """, (resellers['Lipa Fresh Mart'], resellers['Taal Kitchen Supply'], resellers['San Jose Meat Corner']))
        reseller_accounts = {email: acc_id for acc_id, email in cur.fetchall()}
        
        # Now update resellers' approved_by_account_id
        cur.execute("""
            UPDATE resellers SET approved_by_account_id = %s, approved_at = %s;
        """, (leader_account_id, today - timedelta(days=68)))
        
        # 5. Department Leaders
        print("Seeding department leaders...")
        cur.execute("""
            INSERT INTO department_leaders (department_id, team_leader_employee_id, team_leader_account_id, started_on) VALUES
            (%s, %s, %s, %s);
        """, (departments['Retail Floor'], employees['Maria Santos'], leader_account_id, today - timedelta(days=100)))
        
        # 6. Meat Types
        print("Seeding meat types...")
        cur.execute("""
            INSERT INTO meat_types (name, description) VALUES
            ('Longganisa', 'Sweet or garlic cured local sausages.'),
            ('Tocino', 'Sweet cured pork pork slices.'),
            ('Tapa', 'Savory marinated beef slices.'),
            ('Sausage', 'Various ready-to-cook frozen sausages.'),
            ('Bacon', 'Premium smoked bacon cuts.'),
            ('Ham', 'Cured ham products.')
            RETURNING meat_type_id, name;
        """)
        meat_types = {name: mt_id for mt_id, name in cur.fetchall()}
        
        # 7. Products
        print("Seeding products...")
        cur.execute("""
            INSERT INTO products (meat_type_id, name, description, unit, base_price, reorder_level, is_active) VALUES
            (%s, 'Pork Garlic Longganisa', 'Batangas Premium frozen longganisa pack for retail and reseller bundles.', 'pack', 60.00, 35, true),
            (%s, 'Tocino Ala Eh', 'Sweet cured frozen meat product for family meals and reseller shelves.', 'pack', 70.00, 28, true),
            (%s, 'Beef Tapa Ala Eh', 'Savory frozen beef tapa pack positioned for premium breakfast meals.', 'pack', 99.00, 18, true),
            (%s, 'Cheesy Overload Sausage', 'Ready-to-cook frozen sausage pack with cheese-forward positioning.', 'pack', 129.00, 50, true),
            (%s, 'Bacon (Smoked)', 'Smoked bacon product for premium breakfast and food service packs.', 'pack', 129.00, 25, true),
            (%s, 'Hungarian Sausage', 'Savory sausage line for frozen goods resellers and family meals.', 'pack', 109.00, 25, true),
            (NULL, 'Deli Beef', 'Gold line processed beef product for regular frozen food buyers.', 'pack', 125.00, 20, true),
            (%s, 'Hamon Ala Eh', 'Seasonal-style ham product with Batangas Premium''s Ala Eh positioning.', 'pack', 99.90, 20, true)
            RETURNING product_id, name;
        """, (
            meat_types['Longganisa'],
            meat_types['Tocino'],
            meat_types['Tapa'],
            meat_types['Sausage'],
            meat_types['Bacon'],
            meat_types['Sausage'],
            meat_types['Ham']
        ))
        products = {name: prod_id for prod_id, name in cur.fetchall()}
        
        # 7.5 Production Runs
        print("Seeding production runs...")
        cur.execute("""
            INSERT INTO production_runs (product_id, created_by_account_id, quantity_planned, quantity_produced, status, production_date) VALUES
            (%s, %s, 55, 55, 'completed', %s),
            (%s, %s, 70, 70, 'completed', %s)
            RETURNING production_id, product_id;
        """, (
            products['Tocino Ala Eh'], leader_account_id, today - timedelta(days=3),
            products['Pork Garlic Longganisa'], leader_account_id, today - timedelta(days=6)
        ))
        prod_runs = cur.fetchall()
        prod_run_tocino = [r[0] for r in prod_runs if r[1] == products['Tocino Ala Eh']][0]
        prod_run_longganisa = [r[0] for r in prod_runs if r[1] == products['Pork Garlic Longganisa']][0]
        
        # 8. Product Batches
        print("Seeding product batches...")
        cur.execute("""
            INSERT INTO product_batches (product_id, production_id, batch_code, source_type, quantity_received, quantity_available, unit, received_date, expiry_date, quality_status, received_by_account_id) VALUES
            (%s, NULL, 'PGL-0612-A', 'direct_received', 80, 54, 'pack', %s, %s, 'approved', %s),
            (%s, %s, 'TAE-0611-B', 'production', 55, 14, 'pack', %s, %s, 'approved', %s),
            (%s, NULL, 'BTA-0610-A', 'direct_received', 30, 17, 'pack', %s, %s, 'approved', %s),
            (%s, NULL, 'COS-0609-C', 'direct_received', 120, 82, 'pack', %s, %s, 'approved', %s),
            (%s, %s, 'PGL-0608-D', 'production', 70, 12, 'pack', %s, %s, 'approved', %s),
            (%s, NULL, 'BTA-0606-R', 'direct_received', 18, 0, 'pack', %s, %s, 'expired', %s)
            RETURNING product_batch_id, batch_code;
        """, (
            products['Pork Garlic Longganisa'], today - timedelta(days=2), today + timedelta(days=5), leader_account_id,
            products['Tocino Ala Eh'], prod_run_tocino, today - timedelta(days=3), today + timedelta(days=2), leader_account_id,
            products['Beef Tapa Ala Eh'], today - timedelta(days=4), today + timedelta(days=6), leader_account_id,
            products['Cheesy Overload Sausage'], today - timedelta(days=5), today + timedelta(days=19), leader_account_id,
            products['Pork Garlic Longganisa'], prod_run_longganisa, today - timedelta(days=6), today + timedelta(days=1), leader_account_id,
            products['Beef Tapa Ala Eh'], today - timedelta(days=8), today - timedelta(days=1), leader_account_id
        ))
        product_batches = {code: b_id for b_id, code in cur.fetchall()}
        
        # 9. Inquiries
        print("Seeding inquiries...")
        cur.execute("""
            INSERT INTO inquiries (name, contact_number, email, business_name, message, status, assigned_team_leader_account_id, reviewed_by_account_id, reviewed_at) VALUES
            ('Carlo Mendoza', '0917 204 1198', 'reseller@lipafresh.test', 'Lipa Fresh Mart', 'Interested in premium Batangas longganisa for retail.', 'approved', %s, %s, %s),
            ('Taal Kitchen Supply', '0918 442 9033', 'orders@taalkitchen.test', 'Taal Kitchen Supply', 'Reseller application from Taal team.', 'approved', %s, %s, %s),
            ('San Jose Meat Corner', '0920 774 5521', 'sjmeatcorner@test.local', 'San Jose Meat Corner', 'Meat corner shop request.', 'approved', %s, %s, %s),
            ('Nica Flores', '0916 313 4819', 'nica@baletemart.test', 'Balete Mini Mart', 'Interested in twice-weekly frozen longganisa, tapa, and tocino supply.', 'assigned', %s, NULL, NULL),
            ('Jomar Garcia', '0921 772 0061', 'jomar@ihaw.test', 'Garcia Ihaw Supply', 'Asking for reseller pricing for weekend grill packages.', 'assigned', %s, NULL, NULL),
            ('Elaine Robles', '0918 224 6408', 'elaine@roblesfg.test', 'Robles Frozen Goods', 'Needs weekly supply list and minimum order quantity.', 'pending', NULL, NULL, NULL)
            RETURNING inquiry_id, business_name;
        """, (
            leader_account_id, leader_account_id, now_dt - timedelta(days=68),
            leader_account_id, leader_account_id, now_dt - timedelta(days=42),
            leader_account_id, leader_account_id, now_dt - timedelta(days=19),
            leader_account_id, leader_account_id
        ))
        inquiries = {name: inq_id for inq_id, name in cur.fetchall()}
        
        # Now update resellers to link to their approved inquiries
        cur.execute("""
            UPDATE resellers SET inquiry_id = %s WHERE business_name = 'Lipa Fresh Mart';
            UPDATE resellers SET inquiry_id = %s WHERE business_name = 'Taal Kitchen Supply';
            UPDATE resellers SET inquiry_id = %s WHERE business_name = 'San Jose Meat Corner';
        """, (inquiries['Lipa Fresh Mart'], inquiries['Taal Kitchen Supply'], inquiries['San Jose Meat Corner']))
        
        # 10. Inquiry Messages
        print("Seeding inquiry messages...")
        cur.execute("""
            INSERT INTO inquiry_messages (inquiry_id, sender_type, sender_account_id, message, created_at) VALUES
            (%s, 'potential_reseller', NULL, 'Do you deliver around Balete every Monday?', %s),
            (%s, 'chatbot', NULL, 'Delivery coverage is reviewed by the assigned team leader after inquiry validation.', %s),
            (%s, 'team_leader', %s, 'Please send your expected weekend order volume so we can check availability.', %s),
            (%s, 'chatbot', NULL, 'The inquiry has been recorded and assigned for review.', %s),
            (%s, 'team_leader', %s, 'We can include Balete in Tuesday and Friday dispatch after account approval.', %s)
        """, (
            inquiries['Balete Mini Mart'], now_dt - timedelta(hours=7),
            inquiries['Balete Mini Mart'], now_dt - timedelta(hours=7, minutes=-1),
            inquiries['Garcia Ihaw Supply'], leader_account_id, now_dt - timedelta(days=1, hours=2),
            inquiries['Robles Frozen Goods'], now_dt - timedelta(hours=1),
            inquiries['Balete Mini Mart'], leader_account_id, now_dt - timedelta(hours=3)
        ))
        
        # 11. Orders
        print("Seeding orders...")
        cur.execute("""
            INSERT INTO orders (order_type, reseller_id, created_by_account_id, status, order_date, total_amount, notes)
            VALUES ('reseller', %s, %s, 'pending', %s, 840.00, 'For Friday morning pickup.')
            RETURNING order_id;
        """, (resellers['Lipa Fresh Mart'], reseller_accounts['reseller@lipafresh.test'], today))
        order1_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
            VALUES (%s, %s, 12, 'pack', 70.00);
        """, (order1_id, products['Tocino Ala Eh']))
        
        # Order 2 (Taal Kitchen Supply - reseller - approved)
        cur.execute("""
            INSERT INTO orders (order_type, reseller_id, created_by_account_id, approved_by_account_id, approved_at, status, order_date, total_amount, notes)
            VALUES ('reseller', %s, %s, %s, %s, 'approved', %s, 1200.00, 'Approved by Maria Santos.')
            RETURNING order_id;
        """, (resellers['Taal Kitchen Supply'], reseller_accounts['orders@taalkitchen.test'], leader_account_id, today - timedelta(days=1), today - timedelta(days=1)))
        order2_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
            VALUES (%s, %s, 20, 'pack', 60.00);
        """, (order2_id, products['Pork Garlic Longganisa']))
        
        # Order 3 (Walk-in - team-leader - fulfilled)
        cur.execute("""
            INSERT INTO orders (order_type, reseller_id, created_by_account_id, approved_by_account_id, approved_at, status, order_date, fulfilled_at, total_amount, notes)
            VALUES ('walk_in', NULL, %s, %s, %s, 'fulfilled', %s, %s, 2322.00, 'Counter sale recorded by team leader.')
            RETURNING order_id;
        """, (leader_account_id, leader_account_id, today - timedelta(days=1), today - timedelta(days=1), today - timedelta(days=1)))
        order3_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
            VALUES (%s, %s, 18, 'pack', 129.00)
            RETURNING order_item_id;
        """, (order3_id, products['Cheesy Overload Sausage']))
        order3_item_id = cur.fetchone()[0]
        
        # Allocate some batch items for Order 3
        cur.execute("""
            INSERT INTO order_batch_allocations (order_item_id, product_batch_id, quantity_allocated)
            VALUES (%s, %s, 18);
        """, (order3_item_id, product_batches['COS-0609-C']))
        
        # Order 4 (San Jose Meat Corner - reseller - fulfilled)
        cur.execute("""
            INSERT INTO orders (order_type, reseller_id, created_by_account_id, approved_by_account_id, approved_at, status, order_date, fulfilled_at, total_amount, notes)
            VALUES ('reseller', %s, %s, %s, %s, 'fulfilled', %s, %s, 990.00, 'Delivered after batch allocation.')
            RETURNING order_id;
        """, (resellers['San Jose Meat Corner'], reseller_accounts['sjmeatcorner@test.local'], leader_account_id, today - timedelta(days=2), today - timedelta(days=2), today - timedelta(days=2)))
        order4_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO order_items (order_id, product_id, quantity, unit, unit_price)
            VALUES (%s, %s, 10, 'pack', 99.00)
            RETURNING order_item_id;
        """, (order4_id, products['Beef Tapa Ala Eh']))
        order4_item_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO order_batch_allocations (order_item_id, product_batch_id, quantity_allocated)
            VALUES (%s, %s, 10);
        """, (order4_item_id, product_batches['BTA-0610-A']))
        
        # 12. Sales Reports
        print("Seeding sales reports...")
        cur.execute("""
            INSERT INTO sales_reports (report_source, submitted_by_account_id, reseller_id, department_id, period_start, period_end, total_sales, total_orders, notes) VALUES
            ('reseller', %s, %s, NULL, %s, %s, 38450.00, 12, 'Longganisa and Tocino Ala Eh bundles moved fastest.'),
            ('team_leader', %s, NULL, %s, %s, %s, 28470.00, 31, 'High counter demand before weekend.'),
            ('reseller', %s, %s, NULL, %s, %s, 21980.00, 7, 'Stable demand from eateries.'),
            ('team_leader', %s, NULL, %s, %s, %s, 19230.00, 24, 'Cold storage batch transfers completed.')
        """, (
            reseller_accounts['reseller@lipafresh.test'], resellers['Lipa Fresh Mart'], today - timedelta(days=7), today - timedelta(days=1),
            leader_account_id, departments['Retail Floor'], today - timedelta(days=1), today - timedelta(days=1),
            reseller_accounts['orders@taalkitchen.test'], resellers['Taal Kitchen Supply'], today - timedelta(days=14), today - timedelta(days=8),
            leader_account_id, departments['Cold Storage'], today - timedelta(days=2), today - timedelta(days=2)
        ))
        
        # 13. Employee Attendance
        print("Seeding employee attendance...")
        cur.execute("""
            INSERT INTO employee_attendance (employee_id, work_date, status, time_in, time_out, recorded_by_account_id) VALUES
            (%s, %s, 'present', '07:48', NULL, %s),
            (%s, %s, 'present', '07:55', NULL, %s),
            (%s, %s, 'late', '08:22', NULL, %s),
            (%s, %s, 'present', '07:51', '17:10', %s)
        """, (
            employees['Maria Santos'], today, leader_account_id,
            employees['Benjie Cruz'], today, leader_account_id,
            employees['Alma Reyes'], today, leader_account_id,
            employees['Benjie Cruz'], today - timedelta(days=1), leader_account_id
        ))
        
        # 14. Employee Tasks
        print("Seeding employee tasks...")
        cur.execute("""
            INSERT INTO employee_tasks (employee_id, assigned_by_account_id, title, status, due_date, completed_at) VALUES
            (%s, %s, 'Prepare reseller pickup packs', 'completed', %s, %s),
            (%s, %s, 'Verify freezer batch labels', 'in_progress', %s, NULL),
            (%s, %s, 'Counter sanitation checklist', 'assigned', %s, NULL),
            (%s, %s, 'Separate near-expiry batches', 'completed', %s, %s)
        """, (
            employees['Benjie Cruz'], leader_account_id, today, now_dt,
            employees['Alma Reyes'], leader_account_id, today,
            employees['Benjie Cruz'], leader_account_id, today + timedelta(days=1),
            employees['Alma Reyes'], leader_account_id, today - timedelta(days=1), now_dt - timedelta(days=1)
        ))
        
        # 15. Employee Merit Evaluations
        print("Seeding employee merit evaluations...")
        cur.execute("""
            INSERT INTO employee_merit_evaluations (employee_id, evaluator_account_id, period_start, period_end, attendance_score, task_score, behavior_score, overall_score, feedback) VALUES
            (%s, %s, %s, %s, 5, 4, 5, 4.67, 'Reliable counter support and accurate order packing.'),
            (%s, %s, %s, %s, 4, 4, 4, 4.00, 'Good batch handling; improve punctuality during opening shift.'),
            (%s, %s, %s, %s, 5, 5, 5, 5.00, 'Consistent team coordination and inquiry follow-through.')
        """, (
            employees['Benjie Cruz'], leader_account_id, today - timedelta(days=7), today - timedelta(days=1),
            employees['Alma Reyes'], leader_account_id, today - timedelta(days=7), today - timedelta(days=1),
            employees['Maria Santos'], leader_account_id, today - timedelta(days=7), today - timedelta(days=1)
        ))
        
        # 16. Forecast Runs & Results
        print("Seeding forecasts...")
        cur.execute("""
            INSERT INTO forecast_runs (run_by_account_id, model_name, input_period_start, input_period_end, forecast_horizon_days, status, started_at, completed_at, notes)
            VALUES (%s, 'Historical sales baseline', %s, %s, 7, 'completed', %s, %s, 'Initial baseline model seed run.')
            RETURNING forecast_run_id;
        """, (owner_account_id, today - timedelta(days=30), today, now_dt - timedelta(days=1, hours=2), now_dt - timedelta(days=1, hours=2)))
        run_id = cur.fetchone()[0]
        
        cur.execute("""
            INSERT INTO forecast_results (forecast_run_id, product_id, forecast_date, predicted_quantity, confidence_lower, confidence_upper) VALUES
            (%s, %s, %s, 52, 42, 61),
            (%s, %s, %s, 39, 31, 45),
            (%s, %s, %s, 21, 14, 26),
            (%s, %s, %s, 74, 66, 86)
        """, (
            run_id, products['Pork Garlic Longganisa'], today + timedelta(days=1),
            run_id, products['Tocino Ala Eh'], today + timedelta(days=1),
            run_id, products['Beef Tapa Ala Eh'], today + timedelta(days=1),
            run_id, products['Cheesy Overload Sausage'], today + timedelta(days=1)
        ))
        
        # 17. Price Adjustments (Product batch price adjustments)
        print("Seeding price adjustments...")
        cur.execute("""
            INSERT INTO product_batch_price_adjustments (product_batch_id, adjustment_type, discount_percent, adjusted_price, reason, starts_at, created_by_account_id) VALUES
            (%s, 'discount_percent', 12.00, NULL, 'Near expiry priority sale', %s, %s),
            (%s, 'fixed_price', NULL, 65.00, 'Two-day shelf-life markdown', %s, %s)
        """, (
            product_batches['PGL-0608-D'], today, owner_account_id,
            product_batches['TAE-0611-B'], today, owner_account_id
        ))
        
        # 18. Alerts
        print("Seeding alerts...")
        cur.execute("""
            INSERT INTO alerts (alert_type, severity, product_id, product_batch_id, raw_material_id, raw_material_batch_id, message, status, triggered_at) VALUES
            ('near_expiry', 'critical', %s, %s, NULL, NULL, '12 packs expire tomorrow. Consider price adjustment or priority sale.', 'open', %s),
            ('low_stock', 'warning', %s, NULL, NULL, NULL, 'Available stock is below reorder level.', 'open', %s),
            ('near_expiry', 'warning', %s, %s, NULL, NULL, '14 packs expire in 2 days.', 'acknowledged', %s),
            ('forecast', 'info', %s, NULL, NULL, NULL, 'Forecast suggests 18%% higher reseller demand next week.', 'open', %s)
        """, (
            products['Pork Garlic Longganisa'], product_batches['PGL-0608-D'], now_dt - timedelta(hours=2),
            products['Beef Tapa Ala Eh'], now_dt - timedelta(hours=4),
            products['Tocino Ala Eh'], product_batches['TAE-0611-B'], now_dt - timedelta(days=1),
            products['Cheesy Overload Sausage'], now_dt - timedelta(hours=8)
        ))
        
        # 19. Activity Logs
        print("Seeding activity logs...")
        cur.execute("""
            INSERT INTO activity_logs (account_id, action, entity_type, entity_id, created_at) VALUES
            (%s, 'approved_reseller_order', 'orders', %s, %s),
            (NULL, 'generated_low_stock_alert', 'products', %s, %s),
            (%s, 'submitted_sales_report', 'sales_reports', 1, %s),
            (%s, 'recorded_attendance', 'departments', %s, %s),
            (%s, 'updated_product_price', 'products', %s, %s),
            (NULL, 'forecast_completed', 'forecast_runs', %s, %s)
        """, (
            leader_account_id, order2_id, now_dt - timedelta(hours=3),
            products['Beef Tapa Ala Eh'], now_dt - timedelta(hours=4),
            reseller_accounts['reseller@lipafresh.test'], now_dt - timedelta(hours=6),
            leader_account_id, departments['Retail Floor'], now_dt - timedelta(hours=8),
            owner_account_id, products['Tocino Ala Eh'], now_dt - timedelta(days=1),
            run_id, now_dt - timedelta(days=1, hours=2)
        ))
        
        # Commit everything
        conn.commit()
        print("Seeding completed successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"Error during seeding: {e}")
        raise e
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    main()
