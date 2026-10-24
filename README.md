# Support AI: e-commerce customer support assistant (production build, step by step)

Answers policy questions from company documents (RAG), looks up orders, applies refund and return rules in code, routes
angry or unclear customers to a human, in English, Hindi and Hinglish.

Stack: Python, PostgreSQL + pgvector (orders and policy search), Redis (rate limit), Gemini (LLM), multilingual-e5 (embeddings).

## First-time setup (Windows PowerShell, from this folder)
    python -m venv .venv
    .\.venv\Scripts\Activate.ps1
    pip install -r requirements.txt
    copy .env.example .env          # then edit .env: put your GEMINI_API_KEY and a LOG_HASH_SECRET
    docker compose up -d            # Postgres (host port 5433) + Redis
    python -m scripts.apply_sql db/migrations/001_refund_audit.sql   # only for databases created before these existed
    python -m scripts.apply_sql db/migrations/002_product_line.sql
    python -m scripts.apply_sql db/migrations/003_doc_params.sql
    python -m scripts.apply_sql db/migrations/004_handoff_tickets.sql
    python -m scripts.seed_db       # fake customers and orders (with deliberate edge cases)
    python -m app.ingest data/policies   # chunk + embed the policy documents (first run downloads ~2 GB)

A brand-new database gets everything from db/init.sql; the migrations are for databases that already exist (they are safe to run twice).

## Check that everything is in place
    python -m scripts.verify_files          # every file present, right size
    python -m scripts.run_all_tests --reseed   # 18 suites, fake LLM, needs Docker (about 40 s)

## Run the website (customer chat page + API)
    python -m uvicorn app.api:app --port 8000
    # open http://localhost:8000   (loading the embedding model takes ~10-20 s at start)
Demo login: pick a test customer (DEMO_LOGIN=true). Handoffs create rows in shop.handoff_tickets.
Production needs: DEMO_LOGIN=false and real identity, SESSION_SECRET and LOG_HASH_SECRET set to long random values, COOKIE_SECURE=true behind HTTPS.

## Try it from the command line (real Gemini)
    python -m app.handle C001 "Mera order #45821 kahan hai?"
    python -m app.handle C005 "Order 45833 return karna hai"

## Evaluate (real Gemini, about 8 minutes, writes nothing permanent)
    python -m evals.eval_e2e > e2e_out.txt 2>&1
    python -m scripts.summarize_log e2e_out.txt

## Layout
    app/        the system: api (website + HTTP API), session, guardrails, router, handle (entry point), flows (rag, order, refund, return), tools, llm, retrieval, ingest
    app/static/ the customer chat page (index.html, app.css, app.js)
    app/tools/  database tools with the security rules (orders, refunds, returns)
    data/       policy documents, policy_params.json (numbers code needs), product_lines.json, evaluation sets
    db/         schema (init.sql) and migrations
    evals/      evaluations (retrieval, answers, router, end to end)
    scripts/    seed, ingest helpers, benchmarks, test runner, file checker, log summarizer
    tests/      18 suites (fake LLM), run with scripts.run_all_tests
    frontend_tests/  optional JavaScript test of the chat page (needs Node.js, see its README)

## Rules that must not be broken
- customer_id always comes from the logged-in session, never from the message or the LLM.
- Refund, return and order decisions are made in code. The LLM only phrases facts, and code checks the phrasing.
- When a policy number changes (e.g. the return window), change the document AND data/policies/policy_params.json together; ingest refuses a mismatch.
- Set LOG_HASH_SECRET to a long random value before any real deployment.

## Not built yet
Real login (only a demo login exists), human-agent page for tickets, FAQ cache, monitoring/alerts, Dockerfile and deployment, load test.
