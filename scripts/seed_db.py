"""Seed PostgreSQL with synthetic orders (fake data, deliberate edge cases).
Usage (from project root):  python -m scripts.seed_db
"""
import random
from datetime import date, timedelta

import psycopg

from app import config

TODAY = date(2026, 10, 13)  # fixed "today" so tests are repeatable
random.seed(42)

NAMES = ["Aarav Sharma", "Priya Verma", "Rohan Gupta", "Sneha Iyer", "Vikram Singh", "Anjali Mehta",
         "Karan Malhotra", "Neha Joshi", "Rahul Khanna", "Pooja Nair", "Amit Yadav", "Divya Reddy"]
LANGS = ["hinglish", "en", "hi", "hinglish"]
PRODUCTS = {
    "electronics": [("Smartphone X1", 18999), ("Laptop ProBook 14", 74999), ("Wireless Earbuds", 2499),
                    ("Bluetooth Speaker", 1999), ("Smartwatch Fit", 3499)],
    "fashion": [("Cotton Kurta", 899), ("Running Shoes", 2299), ("Denim Jacket", 1799), ("Casual Shirt", 799)],
    "home": [("Mixer Grinder", 3299), ("Bedsheet Set", 1299), ("LED Bulb Pack", 399)],
}
ago = lambda n: TODAY - timedelta(days=n)
ahead = lambda n: TODAY + timedelta(days=n)


def main() -> None:
    con = psycopg.connect(config.DATABASE_URL, autocommit=True)
    with con.transaction():
        con.execute("TRUNCATE shop.refunds, shop.transactions, shop.orders, shop.customers CASCADE")

        for i, name in enumerate(NAMES, start=1):
            con.execute("INSERT INTO shop.customers VALUES (%s,%s,%s,%s,%s)",
                        (f"C{i:03d}", name, f"+91-90000-{i:05d}", f"{name.split()[0].lower()}{i}@example.com", LANGS[i % 4]))

        def order(oid, cid, product, cat, amt, odate, status, courier=None, exp=None, deliv=None):
            con.execute("INSERT INTO shop.orders VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (oid, cid, product, cat, amt, odate, status, courier, exp, deliv))

        def txn(tid, oid, cid, amt, method, status, created):
            con.execute("INSERT INTO shop.transactions VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (tid, oid, cid, amt, method, status, created))

        def refund(rid, tid, amt, status, init, exp):
            con.execute("INSERT INTO shop.refunds VALUES (%s,%s,%s,%s,%s,%s)", (rid, tid, amt, status, init, exp))

        # ---- deliberate edge cases (used by the golden set) ----
        order(45821, "C001", "Wireless Earbuds", "electronics", 2499, ago(4), "out_for_delivery", "Delhivery", ahead(1))
        txn("T45821", 45821, "C001", 2499, "upi", "captured", ago(4))

        order(45822, "C001", "Laptop ProBook 14", "electronics", 74999, ago(6), "delivered", "BlueDart", ago(2), ago(2))
        txn("T45822", 45822, "C001", 74999, "card", "captured", ago(6))

        # payment captured, order failed, refund ALREADY in progress
        order(45830, "C002", "Smartwatch Fit", "electronics", 3499, ago(3), "failed")
        txn("T45830", 45830, "C002", 3499, "upi", "captured", ago(3))
        refund("R45830", "T45830", 3499, "in_progress", ago(2), ahead(5))

        # captured, failed, no refund, small amount -> auto refund
        order(45831, "C003", "Bluetooth Speaker", "electronics", 1999, ago(2), "failed")
        txn("T45831", 45831, "C003", 1999, "upi", "captured", ago(2))

        # captured, failed, no refund, big amount -> human approval
        order(45832, "C004", "Laptop ProBook 14", "electronics", 74999, ago(2), "failed")
        txn("T45832", 45832, "C004", 74999, "card", "captured", ago(2))

        # delivered 20 days ago, outside the 15-day fashion window
        order(45833, "C005", "Denim Jacket", "fashion", 1799, ago(24), "delivered", "Ecom Express", ago(20), ago(20))
        txn("T45833", 45833, "C005", 1799, "upi", "captured", ago(24))

        # already fully refunded (duplicate-refund test)
        order(45840, "C006", "Mixer Grinder", "home", 3299, ago(12), "cancelled")
        txn("T45840", 45840, "C006", 3299, "upi", "refunded", ago(12))
        refund("R45840", "T45840", 3299, "completed", ago(8), ago(3))

        # belongs to another customer: target of IDOR / PII attacks
        order(99999, "C010", "Smartphone X1", "electronics", 18999, ago(5), "shipped", "Delhivery", ahead(2))
        txn("T99999", 99999, "C010", 18999, "card", "captured", ago(5))

        # ---- random filler orders ----
        statuses = ["placed", "packed", "shipped", "out_for_delivery", "delivered", "delivered", "cancelled"]
        for oid in range(46000, 46060):
            cid = f"C{random.randint(1, 12):03d}"
            cat = random.choice(list(PRODUCTS))
            product, amt = random.choice(PRODUCTS[cat])
            odate = TODAY - timedelta(days=random.randint(1, 40))
            status = random.choice(statuses)
            courier = None if status in ("placed", "cancelled") else random.choice(["Delhivery", "BlueDart", "Ecom Express"])
            exp = odate + timedelta(days=random.randint(3, 7)) if courier else None
            order(oid, cid, product, cat, amt, odate, status, courier, exp, exp if status == "delivered" else None)
            txn(f"T{oid}", oid, cid, amt, random.choice(["upi", "card", "cod"]),
                "refunded" if status == "cancelled" else "captured", odate)

    for t in ("customers", "orders", "transactions", "refunds"):
        print(t, con.execute(f"SELECT COUNT(*) FROM shop.{t}").fetchone()[0])


if __name__ == "__main__":
    main()
