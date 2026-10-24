"""Fixed-window rate limiter on Redis (Step 6b).

allow(key) -> True while the key has sent <= limit messages in the current window.

FAIL-OPEN: if Redis is down or slow the message is allowed (a warning is logged), because a cache outage must not
take customer support down. Two protections keep a dead Redis cheap:
  1. the client is built WITHOUT redis-py's built-in retries (measured: with them, one call to a dead Redis took 3-5 s)
  2. a small circuit breaker: after a failure, Redis is not tried again for BREAKER_SECONDS
Note: a fixed window allows a short burst at a window boundary (up to 2x limit). Use a sliding window or token
bucket if that matters.
"""
import logging
import time

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app import config

log = logging.getLogger("ratelimit")
BREAKER_SECONDS = 5


def make_client(url: str | None = None) -> redis.Redis:
    return redis.Redis.from_url(url or config.REDIS_URL, socket_connect_timeout=0.2, socket_timeout=0.2,
                                retry=Retry(NoBackoff(), 0))


class RateLimiter:
    def __init__(self, client=None, limit: int | None = None, window_seconds: int = 60, clock=time.time):
        self.client = client or make_client()
        self.limit = limit if limit is not None else config.RATE_LIMIT_PER_MINUTE
        self.window = window_seconds
        self.clock = clock
        self._down_until = 0.0

    def allow(self, key: str) -> bool:
        now = self.clock()
        if now < self._down_until:
            return True                                   # breaker open: do not touch Redis
        redis_key = f"rl:{key}:{int(now // self.window)}"
        try:
            pipe = self.client.pipeline()
            pipe.incr(redis_key)
            pipe.expire(redis_key, self.window * 2)
            count = pipe.execute()[0]
        except redis.RedisError as e:
            self._down_until = now + BREAKER_SECONDS
            log.warning("rate limiter unavailable (%s): allowing messages for %ss", type(e).__name__, BREAKER_SECONDS)
            return True
        return count <= self.limit
