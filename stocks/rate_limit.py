import math
import time
from dataclasses import dataclass

import redis
from django.conf import settings
from redis.exceptions import NoScriptError


LUA_TOKEN_BUCKET = """
-- KEYS[1] = bucket key
-- ARGV[1] = capacity
-- ARGV[2] = refill_rate_per_sec
-- ARGV[3] = now_us
-- ARGV[4] = requested_tokens (optional, default 1)

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now_us = tonumber(ARGV[3])
local requested = tonumber(ARGV[4]) or 1

if not capacity or capacity <= 0 then
  return redis.error_reply("invalid capacity")
end

if not rate or rate <= 0 then
  return redis.error_reply("invalid refill rate")
end

if not now_us or now_us < 0 then
  return redis.error_reply("invalid now_us")
end

if requested <= 0 then
  return redis.error_reply("invalid requested tokens")
end

local data = redis.call("HMGET", key, "tokens", "last_refill_us")
local tokens = tonumber(data[1])
local last_refill_us = tonumber(data[2])

if tokens == nil or last_refill_us == nil then
  tokens = capacity
  last_refill_us = now_us
else
  local elapsed_us = now_us - last_refill_us
  if elapsed_us < 0 then
    elapsed_us = 0
  end

  if elapsed_us > 0 then
    if tokens < capacity then
      local add = math.floor((elapsed_us * rate) / 1000000)
      if add > 0 then
        tokens = math.min(capacity, tokens + add)
        local consumed_us = math.floor((add * 1000000) / rate)
        last_refill_us = last_refill_us + consumed_us
      end
    end

    if tokens >= capacity then
      tokens = capacity
      last_refill_us = now_us
    end
  end
end

local allowed = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
end

local retry_after_us = 0
if allowed == 0 then
  local missing = requested - tokens
  retry_after_us = math.ceil((missing * 1000000) / rate)
end

redis.call("HMSET",
  key,
  "tokens", tostring(tokens),
  "last_refill_us", tostring(last_refill_us)
)

local full_refill_sec = math.ceil(capacity / rate)
local ttl_sec = math.max(full_refill_sec * 2, 1)
redis.call("EXPIRE", key, ttl_sec)

return {
  allowed,
  tokens,
  retry_after_us
}
"""


@dataclass
class BucketResult:
    allowed: bool
    remaining_tokens: int
    retry_after_seconds: int


class RedisTokenBucket:
    def __init__(
        self,
        key: str,
        capacity: int,
        refill_rate_per_sec: float,
        redis_url: str,
    ):
        self.key = key
        self.capacity = capacity
        self.refill_rate_per_sec = refill_rate_per_sec
        self.redis_url = redis_url
        self._sha = None
        self._client = None

    @property
    def redis_client(self):
        if self._client is None:
            self._client = redis.Redis.from_url(self.redis_url)
        return self._client

    def _load_script(self):
        self._sha = self.redis_client.script_load(LUA_TOKEN_BUCKET)
        return self._sha

    def consume(self, tokens: int = 1) -> BucketResult:
        if tokens <= 0:
            raise ValueError("tokens must be greater than 0")

        now_us = time.time_ns() // 1000
        client = self.redis_client

        if self._sha is None:
            self._load_script()

        try:
            result = client.evalsha(
                self._sha,
                1,
                self.key,
                self.capacity,
                self.refill_rate_per_sec,
                now_us,
                tokens,
            )
        except NoScriptError:
            self._load_script()
            result = client.evalsha(
                self._sha,
                1,
                self.key,
                self.capacity,
                self.refill_rate_per_sec,
                now_us,
                tokens,
            )

        allowed, remaining_tokens, retry_after_us = result
        allowed = bool(int(allowed))
        remaining_tokens = int(remaining_tokens)
        retry_after_us = int(retry_after_us)

        retry_after_seconds = (
            0 if allowed else max(1, math.ceil(retry_after_us / 1_000_000))
        )

        return BucketResult(
            allowed=allowed,
            remaining_tokens=remaining_tokens,
            retry_after_seconds=retry_after_seconds,
        )

    def wait_for_slot(
        self,
        timeout: float = 15.0,
        interval: float = 0.2,
        tokens: int = 1,
    ) -> bool:
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            if self.consume(tokens=tokens).allowed:
                return True
            time.sleep(interval)

        return False


def get_openai_bucket() -> RedisTokenBucket:
    redis_url = (
        getattr(settings, "OPENAI_BUCKET_REDIS_URL", None)
        or getattr(settings, "REDIS_URL", None)
        or "redis://redis:6379/3"
    )

    return RedisTokenBucket(
        key=getattr(settings, "OPENAI_BUCKET_KEY", "rate_limit:openai"),
        capacity=int(getattr(settings, "OPENAI_BUCKET_CAPACITY", 2)),
        refill_rate_per_sec=float(getattr(settings, "OPENAI_BUCKET_REFILL_RATE", 1)),
        redis_url=redis_url,
    )


def get_finnhub_bucket() -> RedisTokenBucket:
    redis_url = (
        getattr(settings, "FINNHUB_BUCKET_REDIS_URL", None)
        or getattr(settings, "REDIS_URL", None)
        or "redis://redis:6379/3"
    )

    return RedisTokenBucket(
        key=getattr(settings, "FINNHUB_BUCKET_KEY", "rate_limit:finnhub"),
        capacity=int(getattr(settings, "FINNHUB_BUCKET_CAPACITY", 2)),
        refill_rate_per_sec=float(getattr(settings, "FINNHUB_BUCKET_REFILL_RATE", 1)),
        redis_url=redis_url,
    )