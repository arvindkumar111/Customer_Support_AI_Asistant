"""Step 8a: the HTTP API and the customer chat page.

Run:  python -m uvicorn app.api:app --port 8000      then open http://localhost:8000

Security rules (each has a test in tests/test_api.py):
  - the customer id comes ONLY from a signed, expiring, HttpOnly session cookie: never from the request body
  - POST requests from another website (wrong Origin) are refused; cookies are SameSite=Lax
  - request bodies are size-limited; every response carries security headers (CSP, nosniff, no-referrer, no-store on /api)
  - an unexpected error returns a generic message, never a traceback
  - demo login (pick a test customer) exists only while DEMO_LOGIN=true; production must provide real identity
"""
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app import config, llm, session
from app.db import get_conn
from app.handle import HUMAN_REPLY, handle_message
from app.tickets import create_ticket

log = logging.getLogger("api")
STATIC = Path(__file__).parent / "static"
STATIC_FILES = {"app.js": "text/javascript", "app.css": "text/css"}     # whitelist: no path traversal possible
COOKIE = "session"
ERROR_REPLY = "Something went wrong on our side. Please try again in a moment. / Kuch gadbad ho gayi. Kripya thodi der baad dobara try karein."


class ChatRequest(BaseModel):
    message: str = Field(max_length=5000)          # the guardrails give a friendly answer above 1000; this is the hard stop


class LoginRequest(BaseModel):
    customer_id: str = Field(max_length=40)


class _KeyedLimiter:
    """handle_message limits by customer id; for anonymous visitors we limit by IP address instead."""

    def __init__(self, limiter, key):
        self.limiter, self.key = limiter, key

    def allow(self, _ignored):
        return self.limiter.allow(self.key)


def create_app(*, generate=None, retriever_factory=None, limiter=None, conn_factory=None) -> FastAPI:
    generate = generate or llm.generate
    conn_factory = conn_factory or get_conn
    shared = {"embedder": None}
    lock = threading.Lock()

    def make_retriever(conn):
        if retriever_factory:
            return retriever_factory(conn)
        from app.retrieval import Retriever
        with lock:                                  # load the embedding model once, share it between requests
            if shared["embedder"] is None:
                from fastembed import TextEmbedding
                shared["embedder"] = TextEmbedding(config.DENSE_MODEL)
        return Retriever(conn, embedder=shared["embedder"])

    @asynccontextmanager
    async def lifespan(app):
        if retriever_factory is None:                # warm up at start, so the first customer does not wait
            c = conn_factory()
            try:
                make_retriever(c).embed_query("warm up")
            finally:
                c.close()
        yield

    app = FastAPI(title="Support AI", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    state = {"limiter": limiter}

    def get_limiter():
        if state["limiter"] is None:
            from app.ratelimit import RateLimiter
            state["limiter"] = RateLimiter()
        return state["limiter"]

    # ------------------------------------------------------------------ middleware
    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"error": "cross_origin_request_refused"}, status_code=403)
            length = request.headers.get("content-length")
            if length and length.isdigit() and int(length) > config.MAX_BODY_BYTES:
                return JSONResponse({"error": "request_too_large"}, status_code=413)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        log.exception("unhandled error on %s", request.url.path)          # the traceback goes to the log, not to the customer
        return JSONResponse({"error": "internal_error", "reply": ERROR_REPLY, "handoff": False}, status_code=500)

    def current_customer(request: Request) -> str | None:
        return session.verify(request.cookies.get(COOKIE))

    # ------------------------------------------------------------------ pages
    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/static/{name}")
    def static_file(name: str):
        if name not in STATIC_FILES:
            raise HTTPException(404)
        return FileResponse(STATIC / name, media_type=STATIC_FILES[name])

    # ------------------------------------------------------------------ health
    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/ready")
    def ready():
        checks = {}
        try:
            c = conn_factory()
            try:
                c.execute("SELECT 1")
            finally:
                c.close()
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "down"
        try:
            lim = get_limiter()
            lim.client.ping() if hasattr(lim, "client") else None
            checks["redis"] = "ok"
        except Exception:
            checks["redis"] = "down (rate limiting fails open)"
        return JSONResponse({"status": "ok" if checks["database"] == "ok" else "unavailable", **checks},
                            status_code=200 if checks["database"] == "ok" else 503)

    # ------------------------------------------------------------------ login (demo)
    @app.get("/api/demo-customers")
    def demo_customers():
        if not config.DEMO_LOGIN:
            raise HTTPException(404)
        c = conn_factory()
        try:
            rows = c.execute("SELECT customer_id, name FROM shop.customers ORDER BY customer_id").fetchall()
        finally:
            c.close()
        return [{"customer_id": r[0], "name": r[1]} for r in rows]

    @app.post("/api/login")
    def login(body: LoginRequest, response: Response):
        if not config.DEMO_LOGIN:
            raise HTTPException(404)
        c = conn_factory()
        try:
            row = c.execute("SELECT name FROM shop.customers WHERE customer_id = %s", (body.customer_id,)).fetchone()
        finally:
            c.close()
        if not row:
            raise HTTPException(401, "unknown customer")
        response.set_cookie(COOKIE, session.sign(body.customer_id), max_age=config.SESSION_TTL_SECONDS, httponly=True,
                            samesite="lax", secure=config.COOKIE_SECURE, path="/")
        return {"customer_id": body.customer_id, "name": row[0]}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE, path="/")
        return {"ok": True}

    @app.get("/api/me")
    def me(request: Request):
        cid = current_customer(request)
        if not cid:
            raise HTTPException(401, "not logged in")
        c = conn_factory()
        try:
            row = c.execute("SELECT name FROM shop.customers WHERE customer_id = %s", (cid,)).fetchone()
        finally:
            c.close()
        if not row:
            raise HTTPException(401, "not logged in")
        return {"customer_id": cid, "name": row[0]}

    # ------------------------------------------------------------------ chat
    @app.post("/api/chat")
    def chat(body: ChatRequest, request: Request):
        cid = current_customer(request)                                 # session only; the body has no customer id
        key = cid or f"ip:{request.client.host if request.client else 'unknown'}"
        conn = conn_factory()
        try:
            reply = handle_message(conn, cid, body.message, retriever=make_retriever(conn), generate=generate,
                                   limiter=_KeyedLimiter(get_limiter(), key))
            ticket_id = None
            if reply.handoff:
                try:
                    ticket_id = create_ticket(conn, cid, reply.request_id, reply.route_reason, reply.handoff_summary)
                except Exception:
                    log.exception("could not create the handoff ticket")   # the customer still gets the handoff message
            return {"reply": reply.text, "handoff": reply.handoff, "ticket_id": ticket_id, "request_id": reply.request_id}
        finally:
            conn.close()

    return app


app = create_app()
