"""Return eligibility rules (DB seeded; the window numbers are injected, so real policy rows are not needed).
Run: python -m tests.test_returns
"""
from datetime import date, timedelta

from app.db import get_conn
from app.tools.returns import check_return_eligibility

TODAY = date(2026, 10, 13)
conn = get_conn()
passed = 0
LOOK = lambda c, pl: {"electronics": 7, "fashion": 15}.get(pl)


def check(name, cond):
    global passed
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        cleanup()
        raise SystemExit(f"test failed: {name}")
    passed += 1


def cleanup():
    conn.execute("DELETE FROM shop.transactions WHERE order_id BETWEEN 88300 AND 88399")
    conn.execute("DELETE FROM shop.orders WHERE order_id BETWEEN 88300 AND 88399")


def make(order_id, category, days_ago, status="delivered"):
    conn.execute("INSERT INTO shop.orders (order_id, customer_id, product, category, amount, order_date, status, delivered_at) "
                 "VALUES (%s, 'C007', 'Test item', %s, 100, %s, %s, %s)",
                 (order_id, category, TODAY - timedelta(days=days_ago + 3), status, TODAY - timedelta(days=days_ago) if status == "delivered" else None))


cleanup()
R = lambda cust, oid, **kw: check_return_eligibility(conn, cust, oid, today=TODAY, window_lookup=kw.get("lookup", LOOK))

d = R("C005", 45833)
check("fashion delivered 20 days ago, window 15 -> outside", d.decision == "outside_window" and d.window_days == 15 and d.days_since_delivery == 20)
check("last return date = delivery + window", d.last_return_date == date(2026, 10, 8))
d = R("C001", 45822)
check("electronics delivered 2 days ago, window 7 -> eligible", d.decision == "eligible" and d.last_return_date == date(2026, 10, 18) and d.days_since_delivery == 2)
check("order still out for delivery -> not delivered", R("C001", 45821).decision == "not_delivered")
check("another customer's order -> not_found", R("C002", 45821).decision == "not_found")
check("C001 cannot check C010's order 99999", R("C001", 99999).decision == "not_found")
check("nonexistent order -> not_found", R("C001", 1234567).decision == "not_found")
check("injection string as order id -> not_found, no crash", R("C001", "45822' OR '1'='1").decision == "not_found")
check("no customer id -> not_found", R(None, 45822).decision == "not_found")

make(88301, "electronics", 7)
make(88302, "electronics", 8)
make(88303, "fashion", 15)
make(88304, "fashion", 16)
check("electronics: exactly 7 days after delivery is still eligible", R("C007", 88301).decision == "eligible")
check("electronics: 8 days -> outside", R("C007", 88302).decision == "outside_window")
check("fashion: exactly 15 days is still eligible", R("C007", 88303).decision == "eligible")
check("fashion: 16 days -> outside", R("C007", 88304).decision == "outside_window")
make(88305, "home", 1)
d = R("C007", 88305)
check("product line with no known window -> a human decides", d.decision == "no_window_known" and d.window_days is None)
check("window lookup returning None -> no_window_known", R("C007", 88301, lookup=lambda c, pl: None).decision == "no_window_known")
make(88306, "electronics", 0)
check("delivered today -> eligible", R("C007", 88306).decision == "eligible")
conn.execute("INSERT INTO shop.orders (order_id, customer_id, product, category, amount, order_date, status) VALUES (88307, 'C007', 'T', 'electronics', 1, %s, 'cancelled')", (TODAY,))
check("cancelled order -> not delivered", R("C007", 88307).decision == "not_delivered")
cleanup()
print(f"\nall {passed} checks passed")
