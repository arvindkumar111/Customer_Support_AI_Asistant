-- Numbers that CODE needs and that are also written in a policy document (e.g. the return window in days).
-- They are loaded from data/policies/policy_params.json by app.ingest, in the same transaction as the document's
-- chunks, and ingest REFUSES a document whose text does not contain the number (so text and number cannot drift apart).
CREATE TABLE IF NOT EXISTS kb.doc_params (
    doc_name TEXT NOT NULL,
    param    TEXT NOT NULL,
    value    INTEGER NOT NULL,
    PRIMARY KEY (doc_name, param)
);
