"""Return eligibility (Step 7c). Decides in CODE whether an order can still be returned; the LLM only phrases it.

The return window (7 days electronics, 15 days fashion) is written in the policy documents. The number code uses is
stored in kb.doc_params by `app.ingest`, and ingest REFUSES a document that does not state that number, so the text
customers read and the number we compute with cannot drift apart.

Rules: the order must belong to this customer (else 'not_found', same as a missing order), must be delivered,
and today - delivered_on <= window  ("within 7 days of delivery": day 7 still counts).
"""
from dataclasses import dataclass
from datetime import date, timedelta

from app.tools.orders import get_order_status

WINDOW_SQL = """
SELECT DISTINCT p.value
FROM kb.doc_params p
JOIN (SELECT DISTINCT doc_name, product_line FROM kb.chunks) c ON c.doc_name = p.doc_name
WHERE p.param = 'return_window_days' AND c.product_line = %s
"""


def get_return_window(conn, product_line: str) -> int | None:
    rows = conn.execute(WINDOW_SQL, (product_line,)).fetchall()
    return rows[0][0] if len(rows) == 1 else None      # none or ambiguous (two different numbers) -> unknown


@dataclass
class ReturnDecision:
    decision: str          # eligible | outside_window | not_delivered | no_window_known | not_found
    reason: str
    order_id: int | None = None
    product: str | None = None
    status: str | None = None
    delivered_on: date | None = None
    window_days: int | None = None
    days_since_delivery: int | None = None
    last_return_date: date | None = None


def check_return_eligibility(conn, customer_id: str, order_id, *, today: date | None = None,
                             window_lookup=get_return_window) -> ReturnDecision:
    today = today or date.today()
    order = get_order_status(conn, customer_id, order_id)
    if order is None:
        return ReturnDecision("not_found", "no_matching_order_for_customer")
    base = dict(order_id=order["order_id"], product=order["product"], status=order["status"])
    if order["status"] != "delivered" or not order["delivered_at"]:
        return ReturnDecision("not_delivered", "order_not_delivered", **base)
    window = window_lookup(conn, order["category"])
    if window is None:
        return ReturnDecision("no_window_known", "no_return_window_for_product_line", delivered_on=order["delivered_at"], **base)
    delivered = order["delivered_at"]
    days_since = (today - delivered).days
    last = delivered + timedelta(days=window)
    kind = "eligible" if days_since <= window else "outside_window"
    return ReturnDecision(kind, f"{days_since}_days_since_delivery_window_{window}", delivered_on=delivered,
                          window_days=window, days_since_delivery=days_since, last_return_date=last, **base)
