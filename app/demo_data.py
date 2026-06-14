from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import count


today = date.today()
now = datetime.now

ids = {
    "inquiry": count(4),
    "reseller": count(4),
    "order": count(5),
    "report": count(5),
    "message": count(6),
    "batch": count(7),
    "alert": count(5),
    "task": count(5),
    "attendance": count(5),
    "evaluation": count(4),
    "log": count(7),
    "adjustment": count(4),
    "forecast": count(5),
    "account": count(6),
}


roles = {
    "owner": {
        "label": "Owner",
        "account_type": "owner",
        "name": "Patricia Manalo",
        "email": "owner@batangaspremium.test",
        "default_section": "dashboard",
    },
    "team-leader": {
        "label": "Team Leader",
        "account_type": "team_leader",
        "name": "Maria Santos",
        "email": "leader@batangaspremium.test",
        "default_section": "dashboard",
    },
    "reseller": {
        "label": "Reseller",
        "account_type": "reseller",
        "name": "Lipa Fresh Mart",
        "email": "reseller@lipafresh.test",
        "default_section": "dashboard",
    },
}


portal_nav = {
    "reseller": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("order", "Place Order", "shopping-basket"),
        ("history", "Order History", "clipboard-list"),
        ("reports", "Sales Reports", "file-up"),
        ("messages", "Messages", "messages-square"),
    ],
    "team-leader": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("sales", "Walk-in Sales", "shopping-cart"),
        ("inventory", "Inventory", "boxes"),
        ("inquiries", "Inquiries", "user-check"),
        ("orders", "Reseller Orders", "clipboard-check"),
        ("employees", "Employees", "users"),
        ("reports", "Reports", "file-text"),
    ],
    "owner": [
        ("dashboard", "Dashboard", "layout-dashboard"),
        ("products", "Products & Prices", "tags"),
        ("reports", "Reports", "bar-chart-3"),
        ("forecasts", "Forecasts", "line-chart"),
        ("accounts", "Accounts", "shield-check"),
        ("logs", "Audit Logs", "scroll-text"),
    ],
}


departments = [
    {"department_id": 1, "department_name": "Retail Floor", "leader": "Maria Santos"},
    {"department_id": 2, "department_name": "Cold Storage", "leader": "Jonel Ramos"},
]


employees = [
    {
        "employee_id": 1,
        "department_id": 1,
        "name": "Maria Santos",
        "position": "Team Leader",
        "employment_status": "active",
        "attendance": "present",
        "task_score": 4.7,
    },
    {
        "employee_id": 2,
        "department_id": 1,
        "name": "Benjie Cruz",
        "position": "Sales Staff",
        "employment_status": "active",
        "attendance": "present",
        "task_score": 4.3,
    },
    {
        "employee_id": 3,
        "department_id": 2,
        "name": "Alma Reyes",
        "position": "Stock Handler",
        "employment_status": "active",
        "attendance": "late",
        "task_score": 4.1,
    },
]


resellers = [
    {
        "reseller_id": 1,
        "business_name": "Lipa Fresh Mart",
        "contact_person": "Carlo Mendoza",
        "email": "reseller@lipafresh.test",
        "contact_number": "0917 204 1198",
        "address": "Poblacion, Lipa City",
        "reseller_status": "active",
        "approved_by": "Maria Santos",
        "created_at": today - timedelta(days=68),
    },
    {
        "reseller_id": 2,
        "business_name": "Taal Kitchen Supply",
        "contact_person": "Rhea Villanueva",
        "email": "orders@taalkitchen.test",
        "contact_number": "0918 442 9033",
        "address": "Taal, Batangas",
        "reseller_status": "active",
        "approved_by": "Maria Santos",
        "created_at": today - timedelta(days=42),
    },
    {
        "reseller_id": 3,
        "business_name": "San Jose Meat Corner",
        "contact_person": "Arnel Dimaapi",
        "email": "sjmeatcorner@test.local",
        "contact_number": "0920 774 5521",
        "address": "San Jose, Batangas",
        "reseller_status": "active",
        "approved_by": "Maria Santos",
        "created_at": today - timedelta(days=19),
    },
]


products = [
    {
        "product_id": 1,
        "name": "Pork Garlic Longganisa",
        "description": "Batangas Premium frozen longganisa pack for retail and reseller bundles.",
        "unit": "pack",
        "base_price": 60.00,
        "reorder_level": 35,
        "available": 124,
        "category": "Longganisa",
    },
    {
        "product_id": 2,
        "name": "Tocino Ala Eh",
        "description": "Sweet cured frozen meat product for family meals and reseller shelves.",
        "unit": "pack",
        "base_price": 70.00,
        "reorder_level": 28,
        "available": 41,
        "category": "Tocino",
    },
    {
        "product_id": 3,
        "name": "Beef Tapa Ala Eh",
        "description": "Savory frozen beef tapa pack positioned for premium breakfast meals.",
        "unit": "pack",
        "base_price": 99.00,
        "reorder_level": 18,
        "available": 17,
        "category": "Tapa",
    },
    {
        "product_id": 4,
        "name": "Cheesy Overload Sausage",
        "description": "Ready-to-cook frozen sausage pack with cheese-forward positioning.",
        "unit": "pack",
        "base_price": 129.00,
        "reorder_level": 50,
        "available": 82,
        "category": "Sausage",
    },
    {
        "product_id": 5,
        "name": "Bacon (Smoked)",
        "description": "Smoked bacon product for premium breakfast and food service packs.",
        "unit": "pack",
        "base_price": 129.00,
        "reorder_level": 25,
        "available": 63,
        "category": "Bacon",
    },
    {
        "product_id": 6,
        "name": "Hungarian Sausage",
        "description": "Savory sausage line for frozen goods resellers and family meals.",
        "unit": "pack",
        "base_price": 109.00,
        "reorder_level": 25,
        "available": 58,
        "category": "Sausage",
    },
    {
        "product_id": 7,
        "name": "Deli Beef",
        "description": "Gold line processed beef product for regular frozen food buyers.",
        "unit": "pack",
        "base_price": 125.00,
        "reorder_level": 20,
        "available": 39,
        "category": "Deli",
    },
    {
        "product_id": 8,
        "name": "Hamon Ala Eh",
        "description": "Seasonal-style ham product with Batangas Premium's Ala Eh positioning.",
        "unit": "pack",
        "base_price": 99.90,
        "reorder_level": 20,
        "available": 46,
        "category": "Ham",
    },
]


product_batches = [
    {
        "product_batch_id": 1,
        "product_id": 1,
        "batch_code": "PGL-0612-A",
        "source_type": "direct_received",
        "quantity_received": 80,
        "quantity_available": 54,
        "unit": "pack",
        "received_date": today - timedelta(days=2),
        "expiry_date": today + timedelta(days=5),
        "quality_status": "approved",
    },
    {
        "product_batch_id": 2,
        "product_id": 2,
        "batch_code": "TAE-0611-B",
        "source_type": "production",
        "quantity_received": 55,
        "quantity_available": 14,
        "unit": "pack",
        "received_date": today - timedelta(days=3),
        "expiry_date": today + timedelta(days=2),
        "quality_status": "approved",
    },
    {
        "product_batch_id": 3,
        "product_id": 3,
        "batch_code": "BTA-0610-A",
        "source_type": "direct_received",
        "quantity_received": 30,
        "quantity_available": 17,
        "unit": "pack",
        "received_date": today - timedelta(days=4),
        "expiry_date": today + timedelta(days=6),
        "quality_status": "approved",
    },
    {
        "product_batch_id": 4,
        "product_id": 4,
        "batch_code": "COS-0609-C",
        "source_type": "direct_received",
        "quantity_received": 120,
        "quantity_available": 82,
        "unit": "pack",
        "received_date": today - timedelta(days=5),
        "expiry_date": today + timedelta(days=19),
        "quality_status": "approved",
    },
    {
        "product_batch_id": 5,
        "product_id": 1,
        "batch_code": "PGL-0608-D",
        "source_type": "production",
        "quantity_received": 70,
        "quantity_available": 12,
        "unit": "pack",
        "received_date": today - timedelta(days=6),
        "expiry_date": today + timedelta(days=1),
        "quality_status": "approved",
    },
    {
        "product_batch_id": 6,
        "product_id": 3,
        "batch_code": "BTA-0606-R",
        "source_type": "direct_received",
        "quantity_received": 18,
        "quantity_available": 0,
        "unit": "pack",
        "received_date": today - timedelta(days=8),
        "expiry_date": today - timedelta(days=1),
        "quality_status": "expired",
    },
]


inquiries = [
    {
        "inquiry_id": 1,
        "name": "Nica Flores",
        "business_name": "Balete Mini Mart",
        "email": "nica@baletemart.test",
        "contact_number": "0916 313 4819",
        "message": "Interested in twice-weekly frozen longganisa, tapa, and tocino supply.",
        "status": "assigned",
        "assigned_to": "Maria Santos",
        "created_at": today - timedelta(days=1),
    },
    {
        "inquiry_id": 2,
        "name": "Jomar Garcia",
        "business_name": "Garcia Ihaw Supply",
        "email": "jomar@ihaw.test",
        "contact_number": "0921 772 0061",
        "message": "Asking for reseller pricing for weekend grill packages.",
        "status": "contacted",
        "assigned_to": "Maria Santos",
        "created_at": today - timedelta(days=3),
    },
    {
        "inquiry_id": 3,
        "name": "Elaine Robles",
        "business_name": "Robles Frozen Goods",
        "email": "elaine@roblesfg.test",
        "contact_number": "0918 224 6408",
        "message": "Needs weekly supply list and minimum order quantity.",
        "status": "pending",
        "assigned_to": "Maria Santos",
        "created_at": today,
    },
]


inquiry_messages = [
    {
        "inquiry_message_id": 1,
        "inquiry_id": 1,
        "sender_type": "potential_reseller",
        "sender": "Nica Flores",
        "message": "Do you deliver around Balete every Monday?",
        "created_at": now() - timedelta(hours=7),
    },
    {
        "inquiry_message_id": 2,
        "inquiry_id": 1,
        "sender_type": "chatbot",
        "sender": "MEATTRACK Assistant",
        "message": "Delivery coverage is reviewed by the assigned team leader after inquiry validation.",
        "created_at": now() - timedelta(hours=7, minutes=-1),
    },
    {
        "inquiry_message_id": 3,
        "inquiry_id": 2,
        "sender_type": "team_leader",
        "sender": "Maria Santos",
        "message": "Please send your expected weekend order volume so we can check availability.",
        "created_at": now() - timedelta(days=1, hours=2),
    },
    {
        "inquiry_message_id": 4,
        "inquiry_id": 3,
        "sender_type": "chatbot",
        "sender": "MEATTRACK Assistant",
        "message": "The inquiry has been recorded and assigned for review.",
        "created_at": now() - timedelta(hours=1),
    },
    {
        "inquiry_message_id": 5,
        "inquiry_id": 1,
        "sender_type": "team_leader",
        "sender": "Maria Santos",
        "message": "We can include Balete in Tuesday and Friday dispatch after account approval.",
        "created_at": now() - timedelta(hours=3),
    },
]


orders = [
    {
        "order_id": 1,
        "order_type": "reseller",
        "reseller_id": 1,
        "reseller": "Lipa Fresh Mart",
        "status": "pending",
        "order_date": today,
        "items": [{"product_id": 2, "name": "Tocino Ala Eh", "quantity": 12, "unit_price": 70.00}],
        "total_amount": 840.00,
        "notes": "For Friday morning pickup.",
    },
    {
        "order_id": 2,
        "order_type": "reseller",
        "reseller_id": 2,
        "reseller": "Taal Kitchen Supply",
        "status": "approved",
        "order_date": today - timedelta(days=1),
        "items": [{"product_id": 1, "name": "Pork Garlic Longganisa", "quantity": 20, "unit_price": 60.00}],
        "total_amount": 1200.00,
        "notes": "Approved by Maria Santos.",
    },
    {
        "order_id": 3,
        "order_type": "walk_in",
        "reseller_id": None,
        "reseller": "Retail counter",
        "status": "fulfilled",
        "order_date": today - timedelta(days=1),
        "items": [{"product_id": 4, "name": "Cheesy Overload Sausage", "quantity": 18, "unit_price": 129.00}],
        "total_amount": 2322.00,
        "notes": "Counter sale recorded by team leader.",
    },
    {
        "order_id": 4,
        "order_type": "reseller",
        "reseller_id": 3,
        "reseller": "San Jose Meat Corner",
        "status": "fulfilled",
        "order_date": today - timedelta(days=2),
        "items": [{"product_id": 3, "name": "Beef Tapa Ala Eh", "quantity": 10, "unit_price": 99.00}],
        "total_amount": 990.00,
        "notes": "Delivered after batch allocation.",
    },
]


sales_reports = [
    {
        "sales_report_id": 1,
        "report_source": "reseller",
        "submitted_by": "Lipa Fresh Mart",
        "period_start": today - timedelta(days=7),
        "period_end": today - timedelta(days=1),
        "total_sales": 38450.00,
        "total_orders": 12,
        "notes": "Longganisa and Tocino Ala Eh bundles moved fastest.",
    },
    {
        "sales_report_id": 2,
        "report_source": "team_leader",
        "submitted_by": "Maria Santos",
        "period_start": today - timedelta(days=1),
        "period_end": today - timedelta(days=1),
        "total_sales": 28470.00,
        "total_orders": 31,
        "notes": "High counter demand before weekend.",
    },
    {
        "sales_report_id": 3,
        "report_source": "reseller",
        "submitted_by": "Taal Kitchen Supply",
        "period_start": today - timedelta(days=14),
        "period_end": today - timedelta(days=8),
        "total_sales": 21980.00,
        "total_orders": 7,
        "notes": "Stable demand from eateries.",
    },
    {
        "sales_report_id": 4,
        "report_source": "team_leader",
        "submitted_by": "Jonel Ramos",
        "period_start": today - timedelta(days=2),
        "period_end": today - timedelta(days=2),
        "total_sales": 19230.00,
        "total_orders": 24,
        "notes": "Cold storage batch transfers completed.",
    },
]


alerts = [
    {
        "alert_id": 1,
        "alert_type": "near_expiry",
        "severity": "critical",
        "subject": "Pork Garlic Longganisa batch PGL-0608-D",
        "message": "12 packs expire tomorrow. Consider price adjustment or priority sale.",
        "status": "open",
        "triggered_at": now() - timedelta(hours=2),
    },
    {
        "alert_id": 2,
        "alert_type": "low_stock",
        "severity": "warning",
        "subject": "Beef Tapa Ala Eh",
        "message": "Available stock is below reorder level.",
        "status": "open",
        "triggered_at": now() - timedelta(hours=4),
    },
    {
        "alert_id": 3,
        "alert_type": "near_expiry",
        "severity": "warning",
        "subject": "Tocino Ala Eh batch TAE-0611-B",
        "message": "14 packs expire in 2 days.",
        "status": "acknowledged",
        "triggered_at": now() - timedelta(days=1),
    },
    {
        "alert_id": 4,
        "alert_type": "forecast",
        "severity": "info",
        "subject": "Cheesy Overload Sausage",
        "message": "Forecast suggests 18% higher reseller demand next week.",
        "status": "open",
        "triggered_at": now() - timedelta(hours=8),
    },
]


tasks = [
    {
        "task_id": 1,
        "employee": "Benjie Cruz",
        "title": "Prepare reseller pickup packs",
        "status": "completed",
        "due_date": today,
    },
    {
        "task_id": 2,
        "employee": "Alma Reyes",
        "title": "Verify freezer batch labels",
        "status": "in_progress",
        "due_date": today,
    },
    {
        "task_id": 3,
        "employee": "Benjie Cruz",
        "title": "Counter sanitation checklist",
        "status": "assigned",
        "due_date": today + timedelta(days=1),
    },
    {
        "task_id": 4,
        "employee": "Alma Reyes",
        "title": "Separate near-expiry batches",
        "status": "completed",
        "due_date": today - timedelta(days=1),
    },
]


attendance = [
    {"attendance_id": 1, "employee": "Maria Santos", "work_date": today, "status": "present", "time_in": "07:48", "time_out": ""},
    {"attendance_id": 2, "employee": "Benjie Cruz", "work_date": today, "status": "present", "time_in": "07:55", "time_out": ""},
    {"attendance_id": 3, "employee": "Alma Reyes", "work_date": today, "status": "late", "time_in": "08:22", "time_out": ""},
    {"attendance_id": 4, "employee": "Benjie Cruz", "work_date": today - timedelta(days=1), "status": "present", "time_in": "07:51", "time_out": "17:10"},
]


evaluations = [
    {
        "evaluation_id": 1,
        "employee": "Benjie Cruz",
        "period": "June Week 2",
        "attendance_score": 5,
        "task_score": 4,
        "behavior_score": 5,
        "overall_score": 4.67,
        "feedback": "Reliable counter support and accurate order packing.",
    },
    {
        "evaluation_id": 2,
        "employee": "Alma Reyes",
        "period": "June Week 2",
        "attendance_score": 4,
        "task_score": 4,
        "behavior_score": 4,
        "overall_score": 4.00,
        "feedback": "Good batch handling; improve punctuality during opening shift.",
    },
    {
        "evaluation_id": 3,
        "employee": "Maria Santos",
        "period": "June Week 2",
        "attendance_score": 5,
        "task_score": 5,
        "behavior_score": 5,
        "overall_score": 5.00,
        "feedback": "Consistent team coordination and inquiry follow-through.",
    },
]


forecasts = [
    {"forecast_result_id": 1, "product": "Pork Garlic Longganisa", "forecast_date": today + timedelta(days=1), "predicted_quantity": 52, "confidence": "42-61 packs"},
    {"forecast_result_id": 2, "product": "Tocino Ala Eh", "forecast_date": today + timedelta(days=1), "predicted_quantity": 39, "confidence": "31-45 packs"},
    {"forecast_result_id": 3, "product": "Beef Tapa Ala Eh", "forecast_date": today + timedelta(days=1), "predicted_quantity": 21, "confidence": "14-26 packs"},
    {"forecast_result_id": 4, "product": "Cheesy Overload Sausage", "forecast_date": today + timedelta(days=1), "predicted_quantity": 74, "confidence": "66-86 packs"},
]


accounts = [
    {"account_id": 1, "account_type": "owner", "name": "Patricia Manalo", "email": "owner@batangaspremium.test", "status": "active"},
    {"account_id": 2, "account_type": "team_leader", "name": "Maria Santos", "email": "leader@batangaspremium.test", "status": "active"},
    {"account_id": 3, "account_type": "reseller", "name": "Lipa Fresh Mart", "email": "reseller@lipafresh.test", "status": "active"},
    {"account_id": 4, "account_type": "reseller", "name": "Taal Kitchen Supply", "email": "orders@taalkitchen.test", "status": "active"},
    {"account_id": 5, "account_type": "reseller", "name": "San Jose Meat Corner", "email": "sjmeatcorner@test.local", "status": "active"},
]


activity_logs = [
    {"activity_log_id": 1, "actor": "Maria Santos", "action": "approved_reseller_order", "entity": "Order #2", "created_at": now() - timedelta(hours=3)},
    {"activity_log_id": 2, "actor": "MEATTRACK", "action": "generated_low_stock_alert", "entity": "Beef Tapa Ala Eh", "created_at": now() - timedelta(hours=4)},
    {"activity_log_id": 3, "actor": "Lipa Fresh Mart", "action": "submitted_sales_report", "entity": "Report #1", "created_at": now() - timedelta(hours=6)},
    {"activity_log_id": 4, "actor": "Maria Santos", "action": "recorded_attendance", "entity": "Retail Floor", "created_at": now() - timedelta(hours=8)},
    {"activity_log_id": 5, "actor": "Owner", "action": "updated_product_price", "entity": "Tocino Ala Eh", "created_at": now() - timedelta(days=1)},
    {"activity_log_id": 6, "actor": "MEATTRACK", "action": "forecast_completed", "entity": "Daily product demand", "created_at": now() - timedelta(days=1, hours=2)},
]


price_adjustments = [
    {
        "adjustment_id": 1,
        "batch_code": "PGL-0608-D",
        "adjustment_type": "discount_percent",
        "value": "12%",
        "reason": "Near expiry priority sale",
        "starts_at": today,
    },
    {
        "adjustment_id": 2,
        "batch_code": "TAE-0611-B",
        "adjustment_type": "fixed_price",
        "value": "PHP 65/pack",
        "reason": "Two-day shelf-life markdown",
        "starts_at": today,
    },
]


def product_by_id(product_id: int) -> dict | None:
    return next((product for product in products if product["product_id"] == product_id), None)


def current_metrics() -> dict:
    fulfilled_sales = sum(order["total_amount"] for order in orders if order["status"] == "fulfilled")
    pending_reseller_orders = sum(1 for order in orders if order["order_type"] == "reseller" and order["status"] == "pending")
    open_alerts = sum(1 for alert in alerts if alert["status"] == "open")
    active_resellers = sum(1 for reseller in resellers if reseller["reseller_status"] == "active")
    total_available = sum(product["available"] for product in products)
    return {
        "fulfilled_sales": fulfilled_sales,
        "pending_reseller_orders": pending_reseller_orders,
        "open_alerts": open_alerts,
        "active_resellers": active_resellers,
        "total_available": total_available,
        "employee_average": round(sum(item["overall_score"] for item in evaluations) / len(evaluations), 2),
    }


def add_log(actor: str, action: str, entity: str) -> None:
    activity_logs.insert(
        0,
        {
            "activity_log_id": next(ids["log"]),
            "actor": actor,
            "action": action,
            "entity": entity,
            "created_at": now(),
        },
    )


def add_inquiry(name: str, business_name: str, email: str, contact_number: str, message: str) -> dict:
    inquiry = {
        "inquiry_id": next(ids["inquiry"]),
        "name": name,
        "business_name": business_name,
        "email": email,
        "contact_number": contact_number,
        "message": message,
        "status": "assigned",
        "assigned_to": "Maria Santos",
        "created_at": today,
    }
    inquiries.insert(0, inquiry)
    inquiry_messages.insert(
        0,
        {
            "inquiry_message_id": next(ids["message"]),
            "inquiry_id": inquiry["inquiry_id"],
            "sender_type": "chatbot",
            "sender": "MEATTRACK Assistant",
            "message": "Inquiry received. Maria Santos will review this reseller application.",
            "created_at": now(),
        },
    )
    add_log("MEATTRACK", "created_inquiry", f"Inquiry #{inquiry['inquiry_id']}")
    return inquiry


def add_reseller_from_inquiry(inquiry_id: int) -> dict | None:
    inquiry = next((item for item in inquiries if item["inquiry_id"] == inquiry_id), None)
    if inquiry is None:
        return None
    inquiry["status"] = "approved"
    reseller = {
        "reseller_id": next(ids["reseller"]),
        "business_name": inquiry["business_name"],
        "contact_person": inquiry["name"],
        "email": inquiry["email"],
        "contact_number": inquiry["contact_number"],
        "address": "Pending onboarding details",
        "reseller_status": "active",
        "approved_by": "Maria Santos",
        "created_at": today,
    }
    resellers.insert(0, reseller)
    accounts.append(
        {
            "account_id": next(ids["account"]),
            "account_type": "reseller",
            "name": reseller["business_name"],
            "email": reseller["email"],
            "status": "active",
        }
    )
    inquiry_messages.insert(
        0,
        {
            "inquiry_message_id": next(ids["message"]),
            "inquiry_id": inquiry_id,
            "sender_type": "team_leader",
            "sender": "Maria Santos",
            "message": "Your reseller application is approved. Account onboarding is ready.",
            "created_at": now(),
        },
    )
    add_log("Maria Santos", "approved_reseller_inquiry", f"Inquiry #{inquiry_id}")
    return reseller


def reject_inquiry(inquiry_id: int) -> bool:
    inquiry = next((item for item in inquiries if item["inquiry_id"] == inquiry_id), None)
    if inquiry is None:
        return False
    inquiry["status"] = "rejected"
    inquiry_messages.insert(
        0,
        {
            "inquiry_message_id": next(ids["message"]),
            "inquiry_id": inquiry_id,
            "sender_type": "team_leader",
            "sender": "Maria Santos",
            "message": "Inquiry reviewed and rejected for now. Applicant may contact the team for clarification.",
            "created_at": now(),
        },
    )
    add_log("Maria Santos", "rejected_reseller_inquiry", f"Inquiry #{inquiry_id}")
    return True


def create_order(role: str, product_id: int, quantity: float, notes: str = "") -> dict:
    product = product_by_id(product_id)
    if product is None:
        raise ValueError("Unknown product")
    order_type = "reseller" if role == "reseller" else "walk_in"
    reseller_id = 1 if role == "reseller" else None
    reseller_name = "Lipa Fresh Mart" if role == "reseller" else "Retail counter"
    status = "pending" if role == "reseller" else "fulfilled"
    total = round(product["base_price"] * quantity, 2)
    order = {
        "order_id": next(ids["order"]),
        "order_type": order_type,
        "reseller_id": reseller_id,
        "reseller": reseller_name,
        "status": status,
        "order_date": today,
        "items": [{"product_id": product_id, "name": product["name"], "quantity": quantity, "unit_price": product["base_price"]}],
        "total_amount": total,
        "notes": notes,
    }
    orders.insert(0, order)
    if role == "team-leader":
        product["available"] = max(0, product["available"] - quantity)
        add_log("Maria Santos", "created_walk_in_sale", f"Order #{order['order_id']}")
    else:
        add_log("Lipa Fresh Mart", "created_reseller_order", f"Order #{order['order_id']}")
    return order


def decide_order(order_id: int, decision: str) -> bool:
    order = next((item for item in orders if item["order_id"] == order_id), None)
    if order is None or order["order_type"] != "reseller":
        return False
    if decision == "approve":
        order["status"] = "approved"
        add_log("Maria Santos", "approved_reseller_order", f"Order #{order_id}")
    elif decision == "reject":
        order["status"] = "rejected"
        add_log("Maria Santos", "rejected_reseller_order", f"Order #{order_id}")
    elif decision == "fulfill":
        order["status"] = "fulfilled"
        for item in order["items"]:
            product = product_by_id(item["product_id"])
            if product:
                product["available"] = max(0, product["available"] - item["quantity"])
        add_log("Maria Santos", "fulfilled_reseller_order", f"Order #{order_id}")
    else:
        return False
    return True


def add_sales_report(source: str, submitted_by: str, period_start: date, period_end: date, total_sales: float, total_orders: int, notes: str) -> dict:
    report = {
        "sales_report_id": next(ids["report"]),
        "report_source": source,
        "submitted_by": submitted_by,
        "period_start": period_start,
        "period_end": period_end,
        "total_sales": round(total_sales, 2),
        "total_orders": total_orders,
        "notes": notes,
    }
    sales_reports.insert(0, report)
    add_log(submitted_by, "submitted_sales_report", f"Report #{report['sales_report_id']}")
    return report


def add_product_batch(product_id: int, batch_code: str, quantity: float, expiry_date: date, source_type: str) -> dict:
    product = product_by_id(product_id)
    if product is None:
        raise ValueError("Unknown product")
    batch = {
        "product_batch_id": next(ids["batch"]),
        "product_id": product_id,
        "batch_code": batch_code,
        "source_type": source_type,
        "quantity_received": quantity,
        "quantity_available": quantity,
        "unit": product["unit"],
        "received_date": today,
        "expiry_date": expiry_date,
        "quality_status": "approved",
    }
    product_batches.insert(0, batch)
    product["available"] += quantity
    days = (expiry_date - today).days
    if days <= 7:
        alerts.insert(
            0,
            {
                "alert_id": next(ids["alert"]),
                "alert_type": "near_expiry",
                "severity": "warning" if days > 2 else "critical",
                "subject": f"{product['name']} batch {batch_code}",
                "message": f"{quantity:g} {product['unit']} expires in {days} day(s).",
                "status": "open",
                "triggered_at": now(),
            },
        )
    add_log("Maria Santos", "registered_product_batch", batch_code)
    return batch
