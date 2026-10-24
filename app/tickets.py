"""Handoff tickets: what a human agent works from. The summary comes from the redacted message."""
INSERT = "INSERT INTO shop.handoff_tickets (customer_id, request_id, reason, summary) VALUES (%s, %s, %s, %s) RETURNING ticket_id"


def create_ticket(conn, customer_id: str | None, request_id: str | None, reason: str, summary: str | None) -> int:
    row = conn.execute(INSERT, (customer_id, request_id, reason, (summary or f"customer={customer_id} | route={reason}")[:2000])).fetchone()
    return row[0]
