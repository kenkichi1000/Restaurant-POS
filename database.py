import sqlite3
from datetime import datetime

DB_PATH = "restaurant.db"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

DB_PATH = "restaurant.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# Rule 10: Helper function for audit logs
def log_audit(conn, entity_type, entity_id, action, staff_id, details):
    conn.execute(
        """INSERT INTO audit_logs (entity_type, entity_id, action, staff_id, details) 
           VALUES (?, ?, ?, ?, ?)""",
        (entity_type, entity_id, action, staff_id, str(details))
    )

# Rule 2, 3, 4, 5: Create Order & Generate KOT
def place_order(order_channel, table_id, staff_id, items):
    """
    items = [
      {"item_id": 1, "quantity": 2, "notes": "No onions", "course_type": "Main"}
    ]
    """
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Rule 3: Validate Dine-In Table requirement
        if order_channel == 'Dine-In' and not table_id:
            raise ValueError("Table ID is required for Dine-In orders.")
            
        # Create Order
        cursor.execute(
            "INSERT INTO orders (order_channel, table_id, staff_id) VALUES (?, ?, ?)",
            (order_channel, table_id, staff_id)
        )
        order_id = cursor.lastrowid
        
        kot_items_to_create = []

        for item in items:
            # Rule 2: Fetch item and verify if active
            cursor.execute("SELECT * FROM menu_items WHERE item_id = ?", (item["item_id"],))
            menu_item = cursor.fetchone()
            
            if not menu_item or not menu_item["is_active"]:
                raise ValueError(f"Menu Item ID {item['item_id']} is either inactive or does not exist.")
            
            if item["quantity"] <= 0:
                raise ValueError("Quantity must be greater than zero.")
            
            # Rule 4: Freeze unit price and tax rate at time of ordering
            cursor.execute("""
                INSERT INTO order_items (order_id, item_id, quantity, unit_price, tax_rate, course_type, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                order_id, 
                item["item_id"], 
                item["quantity"], 
                menu_item["price"], 
                menu_item["tax_rate"],
                item.get("course_type", "Main"),
                item.get("notes", "")
            ))
            order_item_id = cursor.lastrowid
            
            # Rule 5: Queue for KOT if item requires prep
            if menu_item["requires_prep"]:
                kot_items_to_create.append({
                    "order_item_id": order_item_id,
                    "quantity": item["quantity"],
                    "notes": item.get("notes", "")
                })

        # Rule 5: Generate KOT if any items require preparation
        if kot_items_to_create:
            cursor.execute("INSERT INTO kots (order_id, status) VALUES (?, 'Queued')", (order_id,))
            kot_id = cursor.lastrowid
            
            for k_item in kot_items_to_create:
                cursor.execute("""
                    INSERT INTO kot_items (kot_id, order_item_id, notes, quantity)
                    VALUES (?, ?, ?, ?)
                """, (kot_id, k_item["order_item_id"], k_item["notes"], k_item["quantity"]))
            
            log_audit(conn, 'KOT', kot_id, 'CREATE', staff_id, f"Generated KOT with {len(kot_items_to_create)} items.")

        # Update table status if Dine-In
        if table_id:
            cursor.execute("UPDATE dining_tables SET status = 'Occupied' WHERE table_id = ?", (table_id,))

        log_audit(conn, 'Order', order_id, 'CREATE', staff_id, f"Order created via {order_channel}.")
        conn.commit()
        return {"status": "success", "order_id": order_id}

    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# Rule 7: Cancel prepared item with authorization and recorded reason
def cancel_order_item(order_item_id, staff_id, manager_id, reason):
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Check manager authorization role
        cursor.execute("SELECT role FROM staff WHERE staff_id = ?", (manager_id,))
        manager = cursor.fetchone()
        if not manager or manager["role"] not in ['Manager', 'Admin', 'Supervisor']:
            raise PermissionError("Authorization required: Only managers can cancel items.")

        # Record Cancellation
        cursor.execute(
            "INSERT INTO item_cancellations (order_item_id, authorized_by, reason) VALUES (?, ?, ?)",
            (order_item_id, manager_id, reason)
        )
        
        # Update order item status
        cursor.execute("UPDATE order_items SET status = 'Cancelled' WHERE order_item_id = ?", (order_item_id,))
        
        log_audit(conn, 'OrderItem', order_item_id, 'CANCEL', staff_id, f"Cancelled by Manager #{manager_id}. Reason: {reason}")
        conn.commit()
        return {"status": "success", "message": "Item cancelled successfully."}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# Rule 8: Generate Bill when all billable items are served or cancelled
def generate_bill(order_id, staff_id):
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        # Check for unserved items
        cursor.execute("""
            SELECT COUNT(*) as unserved_count FROM order_items 
            WHERE order_id = ? AND status NOT IN ('Served', 'Cancelled')
        """, (order_id,))
        result = cursor.fetchone()
        
        if result["unserved_count"] > 0:
            raise ValueError("Rule 8 Violation: Cannot bill order while active items are unserved.")

        # Calculate Totals
        cursor.execute("""
            SELECT unit_price, quantity, tax_rate, discount FROM order_items 
            WHERE order_id = ? AND status = 'Served'
        """, (order_id,))
        items = cursor.fetchall()
        
        subtotal = 0.0
        tax_total = 0.0
        discount_total = 0.0
        
        for item in items:
            item_sub = (item["unit_price"] * item["quantity"]) - item["discount"]
            item_tax = item_sub * (item["tax_rate"] / 100.0)
            
            subtotal += item_sub
            tax_total += item_tax
            discount_total += item["discount"]

        grand_total = subtotal + tax_total

        # Insert Bill
        cursor.execute("""
            INSERT INTO bills (order_id, subtotal, tax_total, discount_total, grand_total)
            VALUES (?, ?, ?, ?, ?)
        """, (order_id, subtotal, tax_total, discount_total, grand_total))
        bill_id = cursor.lastrowid

        cursor.execute("UPDATE orders SET status = 'Billed' WHERE order_id = ?", (order_id,))
        log_audit(conn, 'Bill', bill_id, 'GENERATE', staff_id, f"Bill generated for Grand Total: ${grand_total:.2f}")
        
        conn.commit()
        return {"status": "success", "bill_id": bill_id, "grand_total": grand_total}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

# Rule 9: Process Payments and handle change
def process_payment(bill_id, payment_method, amount_paid, staff_id):
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute("SELECT grand_total, order_id FROM bills WHERE bill_id = ?", (bill_id,))
        bill = cursor.fetchone()
        
        if not bill:
            raise ValueError("Bill not found.")
            
        grand_total = bill["grand_total"]
        
        if amount_paid < grand_total:
            raise ValueError(f"Insufficient Payment: Amount paid (${amount_paid:.2f}) is less than total (${grand_total:.2f}).")
            
        change_given = amount_paid - grand_total
        
        cursor.execute("""
            INSERT INTO payments (bill_id, payment_method, amount_paid, change_given)
            VALUES (?, ?, ?, ?)
        """, (bill_id, payment_method, amount_paid, change_given))
        
        # Mark bill & order as paid/closed
        cursor.execute("UPDATE bills SET status = 'Paid' WHERE bill_id = ?", (bill_id,))
        cursor.execute("UPDATE orders SET status = 'Closed' WHERE order_id = ?", (bill["order_id"],))
        
        # Release dining table
        cursor.execute("""
            UPDATE dining_tables SET status = 'Available' 
            WHERE table_id = (SELECT table_id FROM orders WHERE order_id = ?)
        """, (bill["order_id"],))

        log_audit(conn, 'Payment', bill_id, 'PAYMENT_RECEIVED', staff_id, f"Paid via {payment_method}. Change given: ${change_given:.2f}")
        conn.commit()
        return {"status": "success", "change_given": change_given}
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
