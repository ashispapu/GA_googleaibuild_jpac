from app.middleware.rate_limit import TokenBucketLimiter


def test_rate_limiter_blocks_then_refills():
    """Burst is allowed, then blocked, then tokens refill over time."""
    t = [0.0]
    lim = TokenBucketLimiter(per_minute=60, burst=2, clock=lambda: t[0])
    assert lim.acquire("k") == 0 and lim.acquire("k") == 0
    assert lim.acquire("k") > 0
    assert lim.acquire("other") == 0  # separate client
    t[0] += 1.1
    assert lim.acquire("k") == 0
