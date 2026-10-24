"""Read-only order tools (Step 4a).

SECURITY RULES (enforced here, in code, never in a prompt):
  1. customer_id comes ONLY from the authenticated session. It is never read from the message or chosen by the LLM.
  2. Every query filters by customer_id. Someone else's order looks exactly like a missing order (None),
     so an attacker cannot even learn whether an order number exists.
  3. All SQL is parameterised. order_id is converted with int() before it reaches the database.
  4. Only the fields needed for an answer are returned: no phone, e-mail, address or name.
"""
import re

ORDER_FIELDS = ("order_id", "product", "category", "amount", "order_date", "status",
                "courier", "expected_delivery", "delivered_at")

ORDER_SQL = f"""
SELECT {", ".join(ORDER_FIELDS)}
FROM shop.orders
WHERE order_id = %(order_id)s AND customer_id = %(customer_id)s
"""

RECENT_SQL = """
SELECT order_id, product, status, order_date
FROM shop.orders
WHERE customer_id = %(customer_id)s
ORDER BY order_date DESC, order_id DESC
LIMIT %(limit)s
"""

# "#45821", "order 45821", "order no. 45821", "order id: 45821". Bare numbers are NOT matched
# on purpose: "74999" in "74999 kat gaya" is an amount, not an order.
ORDER_ID_PATTERN = re.compile(r"(?:#|order\s*(?:no\.?|number|id)?\s*[:#]?\s*)(\d{4,9})", re.IGNORECASE)


def extract_order_ids(text: str) -> list[int]:
    return list(dict.fromkeys(int(m) for m in ORDER_ID_PATTERN.findall(text)))


def get_order_status(conn, customer_id: str, order_id) -> dict | None:
    """Returns the order if it belongs to customer_id, otherwise None (same answer for 'not yours' and 'not found')."""
    try:
        order_id = int(order_id)
    except (TypeError, ValueError):
        return None
    if not customer_id or not (0 < order_id < 2**31):
        return None
    row = conn.execute(ORDER_SQL, {"order_id": order_id, "customer_id": customer_id}).fetchone()
    return dict(zip(ORDER_FIELDS, row)) if row else None


def list_recent_orders(conn, customer_id: str, limit: int = 5) -> list[dict]:
    """Used when the customer asks 'where is my order?' without an order number."""
    if not customer_id:
        return []
    rows = conn.execute(RECENT_SQL, {"customer_id": customer_id, "limit": max(1, min(int(limit), 10))}).fetchall()
    return [dict(zip(("order_id", "product", "status", "order_date"), r)) for r in rows]
