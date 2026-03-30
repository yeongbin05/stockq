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

    -- 중요:
    -- 버킷이 full이면 지난 idle 시간을 더 이상 들고 있으면 안 됨
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