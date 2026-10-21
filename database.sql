-- GourmetPOS schema (SQLite)

-- 1. Staff
CREATE TABLE IF NOT EXISTS staff (
    staff_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(100) NOT NULL,
    role VARCHAR(50) NOT NULL, -- 'Waiter', 'Chef', 'Cashier', 'Supervisor', 'Manager', 'Admin'
    is_active BOOLEAN NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Tables
CREATE TABLE IF NOT EXISTS dining_tables (
    table_id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_number VARCHAR(20) UNIQUE NOT NULL,
    capacity INTEGER NOT NULL DEFAULT 4,
    status VARCHAR(20) NOT NULL DEFAULT 'Available'
        CHECK (status IN ('Available', 'Occupied', 'Reserved'))
);

-- 3. Menu
CREATE TABLE IF NOT EXISTS categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(50) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS menu_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER REFERENCES categories(category_id),
    name VARCHAR(100) NOT NULL,
    price DECIMAL(10,2) NOT NULL CHECK (price >= 0),
    tax_rate DECIMAL(5,2) NOT NULL DEFAULT 8.00 CHECK (tax_rate >= 0),
    requires_prep BOOLEAN NOT NULL DEFAULT 1,  -- Rule 5
    is_active BOOLEAN NOT NULL DEFAULT 1,      -- Rule 2
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 4. Orders
CREATE TABLE IF NOT EXISTS orders (
    order_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_channel VARCHAR(20) NOT NULL
        CHECK (order_channel IN ('Dine-In', 'Takeaway', 'Delivery')),  -- Rule 3
    table_id INTEGER REFERENCES dining_tables(table_id),
    staff_id INTEGER NOT NULL REFERENCES staff(staff_id),
    status VARCHAR(20) NOT NULL DEFAULT 'Open'
        CHECK (status IN ('Open', 'Billed', 'Closed', 'Cancelled')),
    discount_amount DECIMAL(10,2) NOT NULL DEFAULT 0.00 CHECK (discount_amount >= 0),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT check_channel_table CHECK (
        (order_channel = 'Dine-In' AND table_id IS NOT NULL) OR
        (order_channel IN ('Takeaway', 'Delivery'))
    )
);

-- 5. Order items (price and tax frozen at order time - Rule 4)
CREATE TABLE IF NOT EXISTS order_items (
    order_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(order_id),
    item_id INTEGER NOT NULL REFERENCES menu_items(item_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price DECIMAL(10,2) NOT NULL,
    tax_rate DECIMAL(5,2) NOT NULL,
    discount DECIMAL(10,2) NOT NULL DEFAULT 0.00 CHECK (discount >= 0),
    course_type VARCHAR(20) NOT NULL DEFAULT 'Main'
        CHECK (course_type IN ('Appetizer', 'Main', 'Dessert', 'Beverage')),
    notes VARCHAR(255),
    -- 'Ready' added: the kitchen flow sets it, the old CHECK rejected it
    status VARCHAR(20) NOT NULL DEFAULT 'Ordered'
        CHECK (status IN ('Ordered', 'Preparing', 'Ready', 'Served', 'Cancelled')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Kitchen tickets
CREATE TABLE IF NOT EXISTS kots (
    kot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(order_id),
    status VARCHAR(20) NOT NULL DEFAULT 'Queued'
        CHECK (status IN ('Queued', 'Preparing', 'Ready', 'Served', 'Cancelled')),  -- Rule 6
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS kot_items (
    kot_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kot_id INTEGER NOT NULL REFERENCES kots(kot_id),
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id),
    notes VARCHAR(255),
    quantity INTEGER NOT NULL CHECK (quantity > 0)
);

-- 7. Cancellations (Rule 7)
CREATE TABLE IF NOT EXISTS item_cancellations (
    cancellation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_item_id INTEGER NOT NULL REFERENCES order_items(order_item_id),
    authorized_by INTEGER NOT NULL REFERENCES staff(staff_id),
    reason VARCHAR(255) NOT NULL,
    cancelled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 8. Bills & payments (Rules 8 and 9)
CREATE TABLE IF NOT EXISTS bills (
    bill_id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL UNIQUE REFERENCES orders(order_id),
    subtotal DECIMAL(10,2) NOT NULL,        -- before discount and tax
    tax_total DECIMAL(10,2) NOT NULL,
    discount_total DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    grand_total DECIMAL(10,2) NOT NULL,     -- subtotal - discount + tax
    status VARCHAR(20) NOT NULL DEFAULT 'Unpaid'
        CHECK (status IN ('Unpaid', 'Partially_Paid', 'Paid')),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bill_id INTEGER NOT NULL REFERENCES bills(bill_id),
    payment_method VARCHAR(20) NOT NULL
        CHECK (payment_method IN ('Cash', 'Card', 'UPI', 'Wallet')),
    amount_paid DECIMAL(10,2) NOT NULL CHECK (amount_paid > 0),   -- amount tendered
    change_given DECIMAL(10,2) NOT NULL DEFAULT 0.00 CHECK (change_given >= 0),
    transaction_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 9. Audit trail (Rule 10)
CREATE TABLE IF NOT EXISTS audit_logs (
    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type VARCHAR(50) NOT NULL,
    entity_id INTEGER NOT NULL,
    action VARCHAR(50) NOT NULL,
    staff_id INTEGER REFERENCES staff(staff_id),
    details TEXT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_order_items_order ON order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_kots_status ON kots(status);
CREATE INDEX IF NOT EXISTS idx_kot_items_kot ON kot_items(kot_id);
CREATE INDEX IF NOT EXISTS idx_payments_bill ON payments(bill_id);
CREATE INDEX IF NOT EXISTS idx_bills_created ON bills(created_at);