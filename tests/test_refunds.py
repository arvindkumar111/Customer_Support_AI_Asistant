"""Refund rules + idempotency + security tests. Needs the seeded database and the refund_audit table.
Run from project root:  python -m tests.test_refunds
These tests create/delete temporary orders 88000-88999 and remove refunds they cause.
"""
import inspect
import threading
from datetime import date

from app.db import get_conn
from app.tools import refunds
from app.tools.refunds import request_refund

TODAY = date(2026, 10, 13)      # same fixed "today" as the seed data
conn = get_conn()
passed = 0


def check(name, cond):
    global passed
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        cleanup()
        raise SystemExit(f"test failed: {name}")
    passed += 1


def cleanup():
    conn.execute("DELETE FROM shop.refunds WHERE txn_id = 'T45831' OR txn_id LIKE 'T88%'")
    conn.execute("DELETE FROM shop.transactions WHERE order_id BETWEEN 88000 AND 88999")
    conn.execute("DELETE FROM shop.orders WHERE order_id BETWEEN 88000 AND 88999")
    conn.execute("DELETE FROM shop.refund_audit WHERE order_id BETWEEN 88000 AND 88999 "
                 "OR order_id IN (45821, 45830, 45831, 45832, 45840, 99999) OR order_id IS NULL")


def make_order(customer, order_id, amount, order_status="failed", txn_status="captured"):
    conn.execute("INSERT INTO shop.orders (order_id, customer_id, product, category, amount, order_date, status) "
                 "VALUES (%s, %s, 'Test item', 'electronics', %s, %s, %s)", (order_id, customer, amount, TODAY, order_status))
    conn.execute("INSERT INTO shop.transactions VALUES (%s, %s, %s, %s, 'upi', %s, %s)",
                 (f"T{order_id}", order_id, customer, amount, txn_status, TODAY))


def refund_count(txn_id):
    return conn.execute("SELECT count(*) FROM shop.refunds WHERE txn_id = %s", (txn_id,)).fetchone()[0]


def audit_count(order_id):
    return conn.execute("SELECT count(*) FROM shop.refund_audit WHERE order_id = %s", (order_id,)).fetchone()[0]


cleanup()
R = lambda cust, oid, **kw: request_refund(conn, cust, oid, today=TODAY, **kw)

# ---- the seeded scenarios ----
d = R("C003", 45831)
check("small failed-payment refund is auto-approved (45831, Rs 1999)", d.decision == "auto_refund_created" and d.amount == 1999)
check("expected date is today + 7 days", d.expected_by == date(2026, 10, 20))
check("exactly one refund row was created", refund_count("T45831") == 1)
d = R("C003", 45831)
check("asking again is idempotent: in progress, still one refund row", d.decision == "already_in_progress" and refund_count("T45831") == 1)
d = R("C002", 45830)
check("existing refund -> real date from the database", d.decision == "already_in_progress" and d.expected_by == date(2026, 10, 18))
check("amount over limit -> human (45832, Rs 74999)", R("C004", 45832).decision == "needs_human" and R("C004", 45832).reason == "amount_over_limit")
check("big refund created nothing", refund_count("T45832") == 0)
check("already refunded order -> completed, no new refund", R("C006", 45840).decision == "already_completed")
d = R("C001", 45821)
check("order that did not fail -> human, no refund", d.decision == "needs_human" and d.reason == "not_a_failed_payment_case" and refund_count("T45821") == 0)

# ---- security ----
check("C002 cannot refund C003's order", R("C002", 45831).decision == "not_found")
check("C001 cannot refund C010's order 99999", R("C001", 99999).decision == "not_found" and refund_count("T99999") == 0)
check("nonexistent order -> not_found", R("C001", 123456).decision == "not_found")
check("injection string as order id -> not_found, no crash", R("C001", "45821' OR '1'='1").decision == "not_found")
check("missing customer id -> not_found", R(None, 45831).decision == "not_found")
params = set(inspect.signature(request_refund).parameters)
check("no parameter exists for amount, account, upi or override",
      not ({"amount", "account", "upi", "destination", "vip", "limit", "override"} & params))

# ---- thresholds ----
make_order("C007", 88001, 5000)
make_order("C007", 88002, 5001)
check("exactly Rs 5000 is auto-approved", R("C007", 88001).decision == "auto_refund_created")
check("Rs 5001 goes to a human", R("C007", 88002).decision == "needs_human")

make_order("C008", 88010, 1500)
d = R("C008", 88010, angry=True)
check("upset customer -> human even for a small amount", d.decision == "needs_human" and d.reason == "customer_upset" and refund_count("T88010") == 0)
d = R("C008", 88010)
check("same order later, calm customer -> approved", d.decision == "auto_refund_created")
d = R("C008", 88010, angry=True)
check("upset customer with refund in progress still gets the real date", d.decision == "already_in_progress" and d.offer_human and d.expected_by is not None)

for i in (88101, 88102, 88103):
    make_order("C011", i, 500)
    assert R("C011", i).decision == "auto_refund_created"
make_order("C011", 88104, 500)
d = R("C011", 88104)
check("4th refund in 30 days -> human (fraud guard)", d.decision == "needs_human" and d.reason == "too_many_recent_refunds")

# ---- concurrency: 8 simultaneous requests for the same order ----
make_order("C009", 88200, 2000)
results, lock = [], threading.Lock()


def worker():
    c = get_conn()
    r = request_refund(c, "C009", 88200, today=TODAY)
    with lock:
        results.append(r.decision)
    c.close()


threads = [threading.Thread(target=worker) for _ in range(8)]
[t.start() for t in threads]
[t.join() for t in threads]
check("8 parallel requests -> exactly one refund created", results.count("auto_refund_created") == 1 and results.count("already_in_progress") == 7)
check("8 parallel requests -> one refund row in the database", refund_count("T88200") == 1)

# ---- audit ----
check("every decision is audited", audit_count(88001) >= 1 and audit_count(88002) >= 1 and audit_count(88200) == 8)
row = conn.execute("SELECT decision, reason, customer_id FROM shop.refund_audit WHERE order_id = 88002").fetchone()
check("audit row records decision, reason and customer", row == ("needs_human", "amount_over_limit", "C007"))

cleanup()
print(f"\nall {passed} checks passed")