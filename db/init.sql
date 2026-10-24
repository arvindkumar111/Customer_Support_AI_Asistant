CREATE EXTENSION IF NOT EXISTS vector;

-- Two schemas keep customer data and the knowledge base separated (easier permissions later).
CREATE SCHEMA IF NOT EXISTS shop;
CREATE SCHEMA IF NOT EXISTS kb;

-- ---------- knowledge base (RAG) ----------
CREATE TABLE IF NOT EXISTS kb.chunks (
    id           UUID PRIMARY KEY,
    doc_name     TEXT NOT NULL,
    section      TEXT,
    category     TEXT,
    chunk_index  INTEGER NOT NULL,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    product_line TEXT NOT NULL DEFAULT 'general',   -- electronics | fashion | home | general
    indexed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding    vector(1024) NOT NULL,
    -- keyword side of hybrid search. 'simple' = no stemming, safe for Hinglish/Hindi tokens.
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON kb.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON kb.chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_doc_name ON kb.chunks (doc_name);
CREATE INDEX IF NOT EXISTS chunks_category ON kb.chunks (category);
CREATE INDEX IF NOT EXISTS chunks_product_line ON kb.chunks (product_line);

-- ---------- orders (used by tools, never by RAG) ----------
CREATE TABLE IF NOT EXISTS shop.customers (
    customer_id TEXT PRIMARY KEY, name TEXT, phone TEXT, email TEXT, language TEXT
);
CREATE TABLE IF NOT EXISTS shop.orders (
    order_id INTEGER PRIMARY KEY,
    customer_id TEXT NOT NULL REFERENCES shop.customers(customer_id),
    product TEXT, category TEXT, amount INTEGER, order_date DATE, status TEXT,
    courier TEXT, expected_delivery DATE, delivered_at DATE
);
CREATE INDEX IF NOT EXISTS orders_customer ON shop.orders (customer_id);
CREATE TABLE IF NOT EXISTS shop.transactions (
    txn_id TEXT PRIMARY KEY,
    order_id INTEGER REFERENCES shop.orders(order_id),
    customer_id TEXT NOT NULL, amount INTEGER, method TEXT, status TEXT, created_at DATE
);
CREATE TABLE IF NOT EXISTS shop.refunds (
    refund_id TEXT PRIMARY KEY,
    txn_id TEXT NOT NULL UNIQUE REFERENCES shop.transactions(txn_id),  -- UNIQUE = database-level idempotency
    amount INTEGER, status TEXT, initiated_at DATE, expected_by DATE
);

-- ---------- refund audit log (same as db/migrations/001_refund_audit.sql) ----------
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

-- ---------- document parameters (same as db/migrations/003_doc_params.sql) ----------
-- Numbers that CODE needs and that are also written in a policy document (e.g. the return window in days).
-- They are loaded from data/policies/policy_params.json by app.ingest, in the same transaction as the document's
-- chunks, and ingest REFUSES a document whose text does not contain the number (so text and number cannot drift apart).
CREATE TABLE IF NOT EXISTS kb.doc_params (
    doc_name TEXT NOT NULL,
    param    TEXT NOT NULL,
    value    INTEGER NOT NULL,
    PRIMARY KEY (doc_name, param)
);

-- ---------- handoff tickets (same as db/migrations/004_handoff_tickets.sql) ----------
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
