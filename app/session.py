"""Signed session tokens (HMAC-SHA256, expiring). The customer id lives ONLY in this signed cookie, so the browser can
neither choose nor change it. No third-party library: standard library only.
In production a real login system issues the identity; this module then only carries it."""
import base64
import hashlib
import hmac
import json
import time

from app import config


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _mac(body: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())


def sign(customer_id: str, *, now: float | None = None, ttl: int | None = None, secret: str | None = None) -> str:
    payload = {"cid": customer_id, "exp": int((now or time.time()) + (ttl or config.SESSION_TTL_SECONDS))}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{body}.{_mac(body, secret or config.SESSION_SECRET)}"


def verify(token: str | None, *, now: float | None = None, secret: str | None = None) -> str | None:
    """Returns the customer id, or None for anything wrong: bad format, bad signature, expired, tampered."""
    try:
        body, sig = (token or "").split(".")
        if not hmac.compare_digest(sig, _mac(body, secret or config.SESSION_SECRET)):
            return None
        payload = json.loads(_unb64(body))
        if payload["exp"] < (now or time.time()):
            return None
        cid = payload["cid"]
        return cid if isinstance(cid, str) and cid else None
    except Exception:
        return None
