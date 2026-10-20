"""Rate-limiter tests against the REAL Redis (docker compose up -d must be running).  Run: python -m tests.test_ratelimit"""
import time
import uuid

import redis

from app import config
from app.ratelimit import RateLimiter

passed = 0


def check(name, cond):
    global passed
    print(("PASS  " if cond else "FAIL  ") + name)
    if not cond:
        raise SystemExit(f"test failed: {name}")
    passed += 1


k = "test-" + uuid.uuid4().hex[:8]
rl = RateLimiter(limit=3, window_seconds=60)
check("first 3 messages are allowed", all(rl.allow(k) for _ in range(3)))
check("4th message in the window is refused", rl.allow(k) is False)
check("5th is still refused", rl.allow(k) is False)
check("another customer is not affected", rl.allow(k + "-other") is True)

# window rollover with a fake clock (no sleeping)
now = [1_000_000.0]
rl2 = RateLimiter(limit=1, window_seconds=60, clock=lambda: now[0])
k2 = "test-" + uuid.uuid4().hex[:8]
check("1st allowed", rl2.allow(k2))
check("2nd refused in the same window", not rl2.allow(k2))
now[0] += 61
check("allowed again in the next window", rl2.allow(k2))

# keys expire by themselves (no memory leak)
r = redis.Redis.from_url(config.REDIS_URL)
ttl = r.ttl(f"rl:{k}:{int(time.time() // 60)}")
check("counter keys carry a TTL", ttl is not None and 0 < ttl <= 120)

# fail-open when Redis is unreachable
from app.ratelimit import BREAKER_SECONDS, make_client

dead = RateLimiter(client=make_client("redis://127.0.0.1:1/0"), limit=1)
t0 = time.perf_counter()
first = dead.allow("x")
first_ms = (time.perf_counter() - t0) * 1000
check("Redis down -> message is ALLOWED (fail open)", first is True and dead.allow("x") is True)
check(f"first failed call costs well under a second (took {first_ms:.0f} ms)", first_ms < 1000)
t0 = time.perf_counter()
dead.allow("x")
check("next calls skip Redis entirely (circuit breaker)", (time.perf_counter() - t0) * 1000 < 20)


class BrokenClient:
    calls = 0

    def pipeline(self):
        BrokenClient.calls += 1
        raise redis.ConnectionError("down")


fake_now = [5000.0]
cb = RateLimiter(client=BrokenClient(), limit=1, clock=lambda: fake_now[0])
cb.allow("a")
cb.allow("a")
check("breaker: only one Redis attempt while it is open", BrokenClient.calls == 1)
fake_now[0] += BREAKER_SECONDS + 1
cb.allow("a")
check("breaker: Redis is tried again after the pause", BrokenClient.calls == 2)
print(f"\nall {passed} checks passed")