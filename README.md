# Support AI (production build, step by step)

Stack: PostgreSQL + pgvector (orders + knowledge base), Redis (cache), FastAPI (later).

## Setup
    python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
    pip install -r requirements.txt
    cp .env.example .env
    docker compose up -d                                   # Postgres (pgvector) + Redis

## Load data
    python -m scripts.seed_db            # fake customers/orders/refunds
    python -m app.ingest data/policies   # chunk + embed policies into kb.chunks
    python scripts/make_eval_data.py     # (re)generate golden + red-team sets

Change a policy file and re-run ingest: its old chunks are replaced in one transaction.