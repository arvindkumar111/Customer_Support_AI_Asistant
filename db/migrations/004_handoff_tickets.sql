-- One row per conversation that needs a human. `summary` is built from the REDACTED message (no phone/card/etc.).
CREATE TABLE IF NOT EXISTS shop.handoff_tickets (
    ticket_id   BIGSERIAL PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    customer_id TEXT,                       -- NULL when the customer was not logged in
    request_id  TEXT,
    reason      TEXT NOT NULL,
    summary     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open'    -- open | in_progress | closed
);
CREATE INDEX IF NOT EXISTS handoff_open ON shop.handoff_tickets (status, created_at);
