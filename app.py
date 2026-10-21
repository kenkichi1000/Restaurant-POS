from flask import Flask, render_template, request, jsonify
from database import get_connection
from decimal import Decimal

app = Flask(
    __name__,
    static_folder="static",
    static_url_path="/static"
)

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/pos")
def pos():
    return render_template("pos.html")

@app.route("/kitchen")
def kitchen():
    return render_template("kitchen.html")

@app.route("/billing")
def billing():
    return render_template("billing.html")


@app.route("/reports")
def reports():
    return render_template("reports.html")


# -----------------------------
# AUDIT LOG
# -----------------------------

def audit(cursor, staff_id, action, table_name, record_id,
          old_value="", new_value=""):

    cursor.execute("""
        INSERT INTO AuditLogs
        (staff_id, action_type, table_name, record_id,
         old_value, new_value)
        VALUES (%s,%s,%s,%s,%s,%s)
    """, (
        staff_id,
        action,
        table_name,
        record_id,
        old_value,
        new_value
    ))


# -----------------------------
# MENU
# -----------------------------

@app.route("/api/menu")
def get_menu():

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            item_id,
            item_name,
            price,
            tax_rate,
            preparation_required
        FROM MenuItems
        WHERE active = TRUE
        ORDER BY item_name
    """)

    data = cursor.fetchall()

    cursor.close()
    db.close()

    return jsonify(data)


# -----------------------------
# TABLES
# -----------------------------

@app.route("/api/tables")
def get_tables():

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT *
        FROM DiningTables
        ORDER BY table_number
    """)

    data = cursor.fetchall()

    cursor.close()
    db.close()

    return jsonify(data)


# -----------------------------
# STAFF
# -----------------------------

@app.route("/api/staff")
def get_staff():

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT staff_id, staff_name, role
        FROM Staff
        WHERE active = TRUE
        ORDER BY staff_name
    """)

    data = cursor.fetchall()

    cursor.close()
    db.close()

    return jsonify(data)


# -----------------------------
# CREATE ORDER
# -----------------------------

@app.route("/api/orders", methods=["POST"])
def create_order():

    data = request.get_json()

    table_id = data.get("table_id")
    staff_id = data.get("staff_id")
    order_type = data.get("order_type", "Dine-In")
    items = data.get("items", [])

    if not staff_id:
        return jsonify({"error": "Staff is required"}), 400

    if not items:
        return jsonify({"error": "Order must contain items"}), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:

        # Create order
        cursor.execute("""
            INSERT INTO Orders
            (table_id, staff_id, order_type)
            VALUES (%s,%s,%s)
        """, (
            table_id if order_type == "Dine-In" else None,
            staff_id,
            order_type
        ))

        order_id = cursor.lastrowid

        preparation_items = []

        # Insert order items
        for item in items:

            item_id = item["item_id"]
            quantity = int(item["quantity"])
            notes = item.get("notes", "")
            discount = Decimal(str(item.get("discount", 0)))

            if quantity <= 0:
                raise ValueError(
                    "Quantity must be greater than zero"
                )

            cursor.execute("""
                SELECT
                    price,
                    tax_rate,
                    preparation_required,
                    active
                FROM MenuItems
                WHERE item_id = %s
            """, (item_id,))

            menu_item = cursor.fetchone()

            if not menu_item:
                raise ValueError("Menu item not found")

            if not menu_item["active"]:
                raise ValueError(
                    "Menu item is not active"
                )

            price = Decimal(str(menu_item["price"]))
            tax_rate = Decimal(str(menu_item["tax_rate"]))

            subtotal = price * quantity
            tax = subtotal * tax_rate / Decimal("100")

            if discount < 0:
                raise ValueError(
                    "Discount cannot be negative"
                )

            if discount > subtotal + tax:
                raise ValueError(
                    "Discount is too high"
                )

            cursor.execute("""
                INSERT INTO OrderItems
                (
                    order_id,
                    item_id,
                    quantity,
                    unit_price,
                    tax_amount,
                    discount_amount,
                    item_notes,
                    item_status
                )
                VALUES
                (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                order_id,
                item_id,
                quantity,
                price,
                tax,
                discount,
                notes,
                "Ordered" if menu_item["preparation_required"]
                else "Served"
            ))

            order_item_id = cursor.lastrowid

            if menu_item["preparation_required"]:

                preparation_items.append({
                    "order_item_id": order_item_id,
                    "quantity": quantity,
                    "notes": notes
                })

        # Create KOT only if preparation is required
        if preparation_items:

            cursor.execute("""
                INSERT INTO KOTs
                (order_id, created_by, status)
                VALUES (%s,%s,'Queued')
            """, (
                order_id,
                staff_id
            ))

            kot_id = cursor.lastrowid

            for item in preparation_items:

                cursor.execute("""
                    INSERT INTO KOTItems
                    (kot_id, order_item_id, quantity, item_notes)
                    VALUES (%s,%s,%s,%s)
                """, (
                    kot_id,
                    item["order_item_id"],
                    item["quantity"],
                    item["notes"]
                ))

            audit(
                cursor,
                staff_id,
                "CREATE_KOT",
                "KOTs",
                kot_id
            )

        # Occupy table
        if order_type == "Dine-In" and table_id:

            cursor.execute("""
                UPDATE DiningTables
                SET status = 'Occupied'
                WHERE table_id = %s
            """, (table_id,))

        audit(
            cursor,
            staff_id,
            "CREATE_ORDER",
            "Orders",
            order_id
        )

        db.commit()

        return jsonify({
            "message": "Order created successfully",
            "order_id": order_id
        })

    except Exception as e:

        db.rollback()

        return jsonify({
            "error": str(e)
        }), 400

    finally:

        cursor.close()
        db.close()


# -----------------------------
# GET ORDERS
# -----------------------------

@app.route("/api/orders")
def get_orders():

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            o.order_id,
            o.order_type,
            o.order_status,
            o.order_time,
            d.table_number,

            COALESCE(
                SUM(
                    oi.quantity * oi.unit_price
                    + oi.tax_amount
                    - oi.discount_amount
                ),
                0
            ) AS total

        FROM Orders o

        LEFT JOIN DiningTables d
            ON o.table_id = d.table_id

        LEFT JOIN OrderItems oi
            ON o.order_id = oi.order_id

        GROUP BY
            o.order_id,
            o.order_type,
            o.order_status,
            o.order_time,
            d.table_number

        ORDER BY o.order_id DESC
    """)

    data = cursor.fetchall()

    cursor.close()
    db.close()

    return jsonify(data)


# -----------------------------
# GET KOTS
# -----------------------------

@app.route("/api/kots")
def get_kots():

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            k.kot_id,
            k.order_id,
            k.status,
            k.created_at,
            oi.order_item_id,
            mi.item_name,
            ki.quantity,
            ki.item_notes

        FROM KOTs k

        JOIN KOTItems ki
            ON k.kot_id = ki.kot_id

        JOIN OrderItems oi
            ON ki.order_item_id = oi.order_item_id

        JOIN MenuItems mi
            ON oi.item_id = mi.item_id

        ORDER BY
            k.kot_id DESC,
            ki.kot_item_id
    """)

    data = cursor.fetchall()

    cursor.close()
    db.close()

    return jsonify(data)


# -----------------------------
# UPDATE KOT
# -----------------------------

@app.route("/api/kots/<int:kot_id>", methods=["PUT"])
def update_kot(kot_id):

    data = request.get_json()

    new_status = data.get("status")
    staff_id = data.get("staff_id", 2)

    allowed = [
        "Queued",
        "Preparing",
        "Ready",
        "Served",
        "Cancelled"
    ]

    if new_status not in allowed:

        return jsonify({
            "error": "Invalid KOT status"
        }), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT status, order_id
            FROM KOTs
            WHERE kot_id = %s
        """, (kot_id,))

        kot = cursor.fetchone()

        if not kot:
            return jsonify({
                "error": "KOT not found"
            }), 404

        old_status = kot["status"]

        transitions = {
            "Queued": ["Preparing", "Cancelled"],
            "Preparing": ["Ready", "Cancelled"],
            "Ready": ["Served"],
            "Served": [],
            "Cancelled": []
        }

        if new_status not in transitions[old_status]:

            return jsonify({
                "error":
                f"Cannot change {old_status} to {new_status}"
            }), 400

        cursor.execute("""
            UPDATE KOTs
            SET status = %s
            WHERE kot_id = %s
        """, (
            new_status,
            kot_id
        ))

        # Update order item statuses
        if new_status == "Preparing":

            cursor.execute("""
                UPDATE OrderItems oi
                JOIN KOTItems ki
                    ON oi.order_item_id = ki.order_item_id
                SET oi.item_status = 'Preparing'
                WHERE ki.kot_id = %s
            """, (kot_id,))

        elif new_status == "Ready":

            cursor.execute("""
                UPDATE OrderItems oi
                JOIN KOTItems ki
                    ON oi.order_item_id = ki.order_item_id
                SET oi.item_status = 'Ready'
                WHERE ki.kot_id = %s
            """, (kot_id,))

        elif new_status == "Served":

            cursor.execute("""
                UPDATE OrderItems oi
                JOIN KOTItems ki
                    ON oi.order_item_id = ki.order_item_id
                SET oi.item_status = 'Served'
                WHERE ki.kot_id = %s
            """, (kot_id,))

        elif new_status == "Cancelled":

            cursor.execute("""
                UPDATE OrderItems oi
                JOIN KOTItems ki
                    ON oi.order_item_id = ki.order_item_id
                SET oi.item_status = 'Cancelled'
                WHERE ki.kot_id = %s
            """, (kot_id,))

        audit(
            cursor,
            staff_id,
            "UPDATE_KOT",
            "KOTs",
            kot_id,
            old_status,
            new_status
        )

        db.commit()

        return jsonify({
            "message": "KOT updated successfully"
        })

    except Exception as e:

        db.rollback()

        return jsonify({
            "error": str(e)
        }), 400

    finally:

        cursor.close()
        db.close()


# -----------------------------
# CANCEL ORDER ITEM
# -----------------------------

@app.route("/api/cancel-item", methods=["POST"])
def cancel_item():

    data = request.get_json()

    order_item_id = data.get("order_item_id")
    staff_id = data.get("staff_id")
    reason = data.get("reason")

    if not order_item_id or not staff_id or not reason:

        return jsonify({
            "error":
            "Order item, authorized staff and reason are required"
        }), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT item_status
            FROM OrderItems
            WHERE order_item_id = %s
        """, (order_item_id,))

        item = cursor.fetchone()

        if not item:

            return jsonify({
                "error": "Order item not found"
            }), 404

        if item["item_status"] in ["Served", "Cancelled"]:

            return jsonify({
                "error": "Item cannot be cancelled"
            }), 400

        cursor.execute("""
            SELECT role
            FROM Staff
            WHERE staff_id = %s
            AND active = TRUE
        """, (staff_id,))

        staff = cursor.fetchone()

        if not staff:

            return jsonify({
                "error": "Invalid staff"
            }), 400

        if staff["role"] not in ["Manager", "Admin"]:

            return jsonify({
                "error":
                "Manager authorization required"
            }), 403

        cursor.execute("""
            UPDATE OrderItems
            SET item_status = 'Cancelled'
            WHERE order_item_id = %s
        """, (order_item_id,))

        cursor.execute("""
            INSERT INTO ItemCancellations
            (order_item_id, authorized_by, reason)
            VALUES (%s,%s,%s)
        """, (
            order_item_id,
            staff_id,
            reason
        ))

        audit(
            cursor,
            staff_id,
            "CANCEL_ITEM",
            "OrderItems",
            order_item_id,
            item["item_status"],
            "Cancelled"
        )

        db.commit()

        return jsonify({
            "message": "Item cancelled successfully"
        })

    except Exception as e:

        db.rollback()

        return jsonify({
            "error": str(e)
        }), 400

    finally:

        cursor.close()
        db.close()


# -----------------------------
# CREATE BILL
# -----------------------------

@app.route("/api/orders/<int:order_id>/bill", methods=["POST"])
def create_bill(order_id):

    data = request.get_json() or {}

    staff_id = data.get("staff_id", 3)

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT *
            FROM Orders
            WHERE order_id = %s
        """, (order_id,))

        order = cursor.fetchone()

        if not order:

            return jsonify({
                "error": "Order not found"
            }), 404

        cursor.execute("""
            SELECT *
            FROM OrderItems
            WHERE order_id = %s
        """, (order_id,))

        items = cursor.fetchall()

        if not items:

            return jsonify({
                "error": "Order has no items"
            }), 400

        for item in items:

            if item["item_status"] not in [
                "Served",
                "Cancelled"
            ]:

                return jsonify({
                    "error":
                    "All billable items must be served or cancelled"
                }), 400

        cursor.execute("""
            SELECT bill_id
            FROM Bills
            WHERE order_id = %s
        """, (order_id,))

        existing = cursor.fetchone()

        if existing:

            return jsonify({
                "error": "Bill already exists",
                "bill_id": existing["bill_id"]
            }), 400

        subtotal = Decimal("0")
        tax = Decimal("0")
        discount = Decimal("0")

        for item in items:

            if item["item_status"] == "Cancelled":
                continue

            subtotal += (
                Decimal(str(item["unit_price"]))
                * item["quantity"]
            )

            tax += Decimal(str(item["tax_amount"]))
            discount += Decimal(
                str(item["discount_amount"])
            )

        total = subtotal + tax - discount

        cursor.execute("""
            INSERT INTO Bills
            (
                order_id,
                subtotal,
                tax_amount,
                discount_amount,
                total_amount
            )
            VALUES
            (%s,%s,%s,%s,%s)
        """, (
            order_id,
            subtotal,
            tax,
            discount,
            total
        ))

        bill_id = cursor.lastrowid

        audit(
            cursor,
            staff_id,
            "CREATE_BILL",
            "Bills",
            bill_id,
            "",
            str(total)
        )

        db.commit()

        return jsonify({
            "message": "Bill created",
            "bill_id": bill_id,
            "subtotal": float(subtotal),
            "tax": float(tax),
            "discount": float(discount),
            "total": float(total)
        })

    except Exception as e:

        db.rollback()

        return jsonify({
            "error": str(e)
        }), 400

    finally:

        cursor.close()
        db.close()


# -----------------------------
# PAY BILL
# -----------------------------

@app.route("/api/payments", methods=["POST"])
def pay_bill():

    data = request.get_json()

    bill_id = data.get("bill_id")
    amount = Decimal(str(data.get("amount", 0)))
    method = data.get("payment_method")
    staff_id = data.get("staff_id", 3)

    if amount <= 0:

        return jsonify({
            "error": "Payment amount must be positive"
        }), 400

    if method not in ["Cash", "Card", "UPI"]:

        return jsonify({
            "error": "Invalid payment method"
        }), 400

    db = get_db()
    cursor = db.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT total_amount, bill_status, order_id
            FROM Bills
            WHERE bill_id = %s
        """, (bill_id,))

        bill = cursor.fetchone()

        if not bill:

            return jsonify({
                "error": "Bill not found"
            }), 404

        if bill["bill_status"] == "Paid":

            return jsonify({
                "error": "Bill is already paid"
            }), 400

        cursor.execute("""
            SELECT COALESCE(SUM(amount), 0) AS paid
            FROM Payments
            WHERE bill_id = %s
        """, (bill_id,))

        paid = Decimal(str(
            cursor.fetchone()["paid"]
        ))

        outstanding = (
            Decimal(str(bill["total_amount"]))
            - paid
        )

        if amount > outstanding:

            return jsonify({
                "error":
                f"Payment exceeds outstanding amount: {outstanding}"
            }), 400

        cursor.execute("""
            INSERT INTO Payments
            (bill_id, payment_method, amount)
            VALUES (%s,%s,%s)
        """, (
            bill_id,
            method,
            amount
        ))

        new_paid = paid + amount

        if new_paid == Decimal(
            str(bill["total_amount"])
        ):

            cursor.execute("""
                UPDATE Bills
                SET bill_status = 'Paid'
                WHERE bill_id = %s
            """, (bill_id,))

            cursor.execute("""
                UPDATE Orders
                SET order_status = 'Completed'
                WHERE order_id = %s
            """, (bill["order_id"],))

            cursor.execute("""
                UPDATE DiningTables d
                JOIN Orders o
                    ON d.table_id = o.table_id
                SET d.status = 'Available'
                WHERE o.order_id = %s
            """, (bill["order_id"],))

        audit(
            cursor,
            staff_id,
            "PAYMENT",
            "Payments",
            cursor.lastrowid,
            "",
            str(amount)
        )

        db.commit()

        return jsonify({
            "message": "Payment successful",
            "paid": float(new_paid),
            "outstanding":
                float(
                    Decimal(str(bill["total_amount"]))
                    - new_paid
                )
        })

    except Exception as e:

        db.rollback()

        return jsonify({
            "error": str(e)
        }), 400

    finally:

        cursor.close()
        db.close()


# -----------------------------
# REPORT
# -----------------------------

@app.route("/api/report")
def report():

    db = get_db()
    cursor = db.cursor(dictionary=True)

    cursor.execute("""
        SELECT
            COUNT(*) AS total_bills,
            COALESCE(SUM(total_amount), 0) AS total_sales
        FROM Bills
        WHERE DATE(created_at) = CURDATE()
        AND bill_status = 'Paid'
    """)

    data = cursor.fetchone()

    cursor.close()
    db.close()

    return jsonify(data)


# -----------------------------
# RUN APPLICATION
# -----------------------------

if __name__ == "__main__":
    app.run(debug=True)