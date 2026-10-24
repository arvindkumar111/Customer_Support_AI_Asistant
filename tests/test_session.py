"""Session token tests (no database).  Run: python -m tests.test_session"""
import base64
import json

from app import session

passed = 0


def check(name, cond):
    global passed
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        raise SystemExit(f"test failed: {name}")
    passed += 1


NOW = 1_800_000_000.0
tok = session.sign("C001", now=NOW, ttl=3600, secret="s1")
check("a fresh token verifies and returns the customer id", session.verify(tok, now=NOW + 10, secret="s1") == "C001")
check("a token signed with another secret is rejected", session.verify(tok, now=NOW + 10, secret="s2") is None)
check("an expired token is rejected", session.verify(tok, now=NOW + 3601, secret="s1") is None)
check("exactly at the expiry second it is still valid, one second later it is not", session.verify(tok, now=NOW + 3600, secret="s1") == "C001" and session.verify(tok, now=NOW + 3601.5, secret="s1") is None)

body, sig = tok.split(".")
forged_payload = base64.urlsafe_b64encode(json.dumps({"cid": "C010", "exp": int(NOW) + 3600}, separators=(",", ":")).encode()).rstrip(b"=").decode()
check("changing the customer id but keeping the old signature is rejected", session.verify(forged_payload + "." + sig, now=NOW, secret="s1") is None)
check("a token with its signature removed is rejected", session.verify(body + ".", now=NOW, secret="s1") is None)
for name, bad in [("None", None), ("empty", ""), ("no dot", "abc"), ("two dots", "a.b.c"), ("garbage", "!!!.???"), ("only a dot", ".")]:
    check(f"garbage token is rejected: {name}", session.verify(bad, now=NOW, secret="s1") is None)

not_json = base64.urlsafe_b64encode(b"not json").rstrip(b"=").decode()
check("a correctly signed but non-JSON body is rejected", session.verify(not_json + "." + session._mac(not_json, "s1"), now=NOW, secret="s1") is None)
numeric = base64.urlsafe_b64encode(json.dumps({"cid": 5, "exp": int(NOW) + 60}).encode()).rstrip(b"=").decode()
check("a signed payload whose customer id is not text is rejected", session.verify(numeric + "." + session._mac(numeric, "s1"), now=NOW, secret="s1") is None)
empty_cid = base64.urlsafe_b64encode(json.dumps({"cid": "", "exp": int(NOW) + 60}).encode()).rstrip(b"=").decode()
check("a signed payload with an empty customer id is rejected", session.verify(empty_cid + "." + session._mac(empty_cid, "s1"), now=NOW, secret="s1") is None)
check("the token never contains the secret", "s1" not in tok.replace("C001", ""))
check("two customers get different tokens", session.sign("C001", now=NOW, secret="s1") != session.sign("C002", now=NOW, secret="s1"))
check("the default secret and TTL come from the configuration", session.verify(session.sign("C003")) == "C003")
print(f"\nall {passed} checks passed")
