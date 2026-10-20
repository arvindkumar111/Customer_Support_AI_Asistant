CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS shop;
CREATE SCHEMA IF NOT EXISTS kb;

CREATE TABLE IF NOT EXISTS kb.chunks (
    id           UUID PRIMARY KEY,
    doc_name     TEXT NOT NULL,
    section      TEXT,
    category     TEXT,
    chunk_index  INTEGER NOT NULL,
    content      TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    indexed_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    embedding    vector(1024) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON kb.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON kb.chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS chunks_doc_name ON kb.chunks (doc_name);
CREATE INDEX IF NOT EXISTS chunks_category ON kb.chunks (category);

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
    txn_id TEXT NOT NULL UNIQUE REFERENCES shop.transactions(txn_id),
    amount INTEGER, status TEXT, initiated_at DATE, expected_by DATE
);