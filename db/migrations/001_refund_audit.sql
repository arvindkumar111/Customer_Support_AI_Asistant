-- Every refund decision (approved, denied, escalated) is written here. Append-only by convention.
CREATE TABLE IF NOT EXISTS shop.refund_audit (
    audit_id    BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    customer_id TEXT,
    order_id    BIGINT,
    txn_id      TEXT,
    decision    TEXT NOT NULL,
    reason      TEXT NOT NULL,
    amount      INTEGER,
    details     JSONB
);
CREATE INDEX IF NOT EXISTS refund_audit_customer ON shop.refund_audit (customer_id, created_at);
