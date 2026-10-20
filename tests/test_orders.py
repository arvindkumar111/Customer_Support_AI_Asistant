"""Security tests for the order tools. Needs the database running and seeded.
Run from project root:  python -m tests.test_orders
"""
from datetime import date

from app.db import get_conn
from app.tools.orders import ORDER_FIELDS, extract_order_ids, get_order_status, list_recent_orders

conn = get_conn()
passed = 0


def check(name, condition):
    global passed
    print(("PASS  " if condition else "FAIL  ") + name)
    if not condition:
        raise SystemExit(f"test failed: {name}")
    passed += 1


o = get_order_status(conn, "C001", 45821)
check("owner sees own order (45821, C001)", o is not None and o["status"] == "out_for_delivery")
check("expected delivery date is the real one", o["expected_delivery"] == date(2026, 10, 14))
check("only whitelisted fields are returned", set(o) == set(ORDER_FIELDS))
check("another customer gets None for 45821 (C002)", get_order_status(conn, "C002", 45821) is None)
check("C001 cannot see C010's order 99999", get_order_status(conn, "C001", 99999) is None)
check("C010 can see own order 99999", get_order_status(conn, "C010", 99999) is not None)
check("nonexistent order -> None", get_order_status(conn, "C001", 1234567) is None)
check("'not yours' and 'not found' look identical", get_order_status(conn, "C001", 99999) == get_order_status(conn, "C001", 1234567))
check("SQL-injection string -> None, no crash", get_order_status(conn, "C001", "45821' OR '1'='1") is None)
check("empty customer_id -> None", get_order_status(conn, "", 45821) is None)
check("None customer_id -> None", get_order_status(conn, None, 45821) is None)
check("huge order id -> None, no crash", get_order_status(conn, "C001", 10**30) is None)
check("negative order id -> None", get_order_status(conn, "C001", -1) is None)
check("customer_id injection attempt -> None", get_order_status(conn, "C001' OR '1'='1", 45821) is None)

recent = list_recent_orders(conn, "C001")
ids = {r["order_id"] for r in recent}
check("recent orders include C001's 45821 and 45822", {45821, 45822} <= ids)
check("recent orders never include 99999", 99999 not in ids)
check("recent orders are limited", len(list_recent_orders(conn, "C001", limit=2)) == 2)
check("unknown customer has no orders", list_recent_orders(conn, "NOPE") == [])

check("extract '#45821'", extract_order_ids("Mera order #45821 kahan hai?") == [45821])
check("extract 'order 45821'", extract_order_ids("Where is order 45821?") == [45821])
check("amount is not an order id", extract_order_ids("74999 kat gaya") == [])
check("no order number -> []", extract_order_ids("Mera order kahan hai?") == [])
check("injection text yields only a safe integer", extract_order_ids("order 45821' OR '1'='1") == [45821])
print(f"\nall {passed} checks passed")