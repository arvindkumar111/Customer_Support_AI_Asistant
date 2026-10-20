"""Refund tool + rules engine (Step 4c). All decisions are made HERE, in code. The LLM never approves anything.

What the LLM/router may pass in: the customer_id from the SESSION, an order number, and an `angry` flag.
What it can NOT pass in: an amount, a destination account/UPI, a "VIP" claim, a limit override.
(The function has no parameter for any of those, so no prompt can set them.)

Checks, in order:
  1. order belongs to this customer, and has a payment transaction   -> else "not_found" (same answer as missing)
  2. a refund already exists for that transaction                   -> tell the customer the real status/date
  3. payment captured AND order failed                              -> else a human decides
  4. amount <= AUTO_REFUND_LIMIT, customer not upset, <= MAX_AUTO_REFUNDS_30D in 30 days -> else a human decides
  5. create the refund (original payment method only) + write an audit row, all in one transaction
Idempotency: a row lock on the transaction + UNIQUE(txn_id) in the database. Ten simultaneous requests -> one refund.
"""
from dataclasses import dataclass
from datetime import date, timedelta

from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

AUTO_REFUND_LIMIT = 5000        # rupees; matches the policy: refunds above Rs 5000 need manual approval
MAX_AUTO_REFUNDS_30D = 3        # fraud guard: more than this many refunds in 30 days -> human
REFUND_DAYS = 7                 # expected_by = today + 7 days (policy says 5-7 BUSINESS days: approximate)

LOOKUP_SQL = """
SELECT o.order_id, o.status, t.txn_id, t.amount, t.status
FROM shop.orders o
JOIN shop.transactions t ON t.order_id = o.order_id AND t.customer_id = o.customer_id
WHERE o.order_id = %(order_id)s AND o.customer_id = %(customer_id)s
ORDER BY t.created_at DESC
LIMIT 1
FOR UPDATE OF t
"""


@dataclass
class RefundDecision:
    decision: str      # auto_refund_created | already_in_progress | already_completed | needs_human | not_found
    reason: str
    order_id: int | None = None
    amount: int | None = None
    refund_id: str | None = None
    expected_by: date | None = None
    offer_human: bool = False


def _audit(conn, customer_id, order_id, txn_id, d: RefundDecision, details: dict) -> None:
    conn.execute(
        "INSERT INTO shop.refund_audit (customer_id, order_id, txn_id, decision, reason, amount, details) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (customer_id, order_id, txn_id, d.decision, d.reason, d.amount, Jsonb(details)))


def _decide(conn, customer_id, row, angry: bool, today: date, details: dict) -> RefundDecision:
    order_id, order_status, txn_id, amount, txn_status = row

    existing = conn.execute(
        "SELECT refund_id, status, expected_by FROM shop.refunds WHERE txn_id = %s", (txn_id,)).fetchone()
    if existing:
        rid, rstatus, expected_by = existing
        kind = "already_completed" if rstatus == "completed" else "already_in_progress"
        return RefundDecision(kind, f"refund_{rstatus}", order_id, amount, rid, expected_by, offer_human=angry)

    if order_status != "failed" or txn_status != "captured":
        return RefundDecision("needs_human", "not_a_failed_payment_case", order_id, amount, offer_human=True)
    if amount > AUTO_REFUND_LIMIT:
        return RefundDecision("needs_human", "amount_over_limit", order_id, amount, offer_human=True)
    if angry:
        return RefundDecision("needs_human", "customer_upset", order_id, amount, offer_human=True)

    recent = conn.execute(
        "SELECT count(*) FROM shop.refunds r JOIN shop.transactions t ON t.txn_id = r.txn_id "
        "WHERE t.customer_id = %s AND r.initiated_at >= %s", (customer_id, today - timedelta(days=30))).fetchone()[0]
    details["refunds_last_30d"] = recent
    if recent >= MAX_AUTO_REFUNDS_30D:
        return RefundDecision("needs_human", "too_many_recent_refunds", order_id, amount, offer_human=True)

    refund_id, expected_by = f"R{order_id}", today + timedelta(days=REFUND_DAYS)
    try:
        with conn.transaction():   # savepoint: a duplicate must not abort the whole request
            conn.execute("INSERT INTO shop.refunds VALUES (%s, %s, %s, 'in_progress', %s, %s)",
                         (refund_id, txn_id, amount, today, expected_by))
    except UniqueViolation:        # a parallel request won the race
        rid, rstatus, exp = conn.execute(
            "SELECT refund_id, status, expected_by FROM shop.refunds WHERE txn_id = %s", (txn_id,)).fetchone()
        return RefundDecision("already_in_progress", "refund_in_progress", order_id, amount, rid, exp)
    return RefundDecision("auto_refund_created", "approved_by_rules", order_id, amount, refund_id, expected_by)


def request_refund(conn, customer_id: str, order_id, *, angry: bool = False, today: date | None = None) -> RefundDecision:
    today = today or date.today()
    details = {"angry": angry, "limit": AUTO_REFUND_LIMIT, "max_30d": MAX_AUTO_REFUNDS_30D}
    try:
        order_id = int(order_id)
        if not customer_id or not (0 < order_id < 2**31):
            raise ValueError
    except (TypeError, ValueError):
        d = RefundDecision("not_found", "invalid_order_or_customer")
        with conn.transaction():
            _audit(conn, str(customer_id)[:50] if customer_id else None, None, None, d, details)
        return d

    with conn.transaction():
        row = conn.execute(LOOKUP_SQL, {"order_id": order_id, "customer_id": customer_id}).fetchone()
        if row is None:
            d = RefundDecision("not_found", "no_matching_order_for_customer", order_id)
            txn_id = None
        else:
            d = _decide(conn, customer_id, row, angry, today, details)
            txn_id = row[2]
        _audit(conn, customer_id, order_id, txn_id, d, details)
    return d