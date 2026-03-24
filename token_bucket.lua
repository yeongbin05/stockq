-- KEYS[1] = bucket key
-- ARGV[1] = capacity
-- ARGV[2] = refill_rate_per_sec
-- ARGV[3] = now_us

local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local rate = tonumber(ARGV[2])
local now_us = tonumber(ARGV[3])

local data = redis.call("HMGET", key, "tokens", "last_refill_us")
local tokens = tonumber(data[1])
local last_refill_us = tonumber(data[2])

if tokens == nil or last_refill_us == nil then
  tokens = capacity
  last_refill_us = now_us
else
  local elapsed_us = now_us - last_refill_us
  if elapsed_us > 0 and tokens < capacity then
    local add = math.floor((elapsed_us * rate) / 1000000)
    if add > 0 then
      tokens = math.min(capacity, tokens + add)
      local consumed_us = math.floor((add * 1000000) / rate)
      last_refill_us = last_refill_us + consumed_us
    end
  end
end

local allowed = 0
if tokens >= 1 then
  tokens = tokens - 1
  allowed = 1
end

redis.call("HSET", key, "tokens", tostring(tokens))
redis.call("HSET", key, "last_refill_us", tostring(last_refill_us))
redis.call("EXPIRE", key, 120)

return allowed