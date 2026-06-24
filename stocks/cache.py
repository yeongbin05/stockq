import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

SUMMARY_CACHE_VERSION = "v1"
SUMMARY_CACHE_TIMEOUT = 60 * 60 * 24


def summary_cache_key(stock_id, date) -> str:
    return f"summary:{SUMMARY_CACHE_VERSION}:{stock_id}:{date.isoformat()}"


def get_cached_summary(stock_id, date):
    key = summary_cache_key(stock_id, date)
    try:
        return cache.get(key)
    except Exception:
        logger.warning("[summary_cache] get failed key=%s", key, exc_info=True)
        return None


def set_cached_summary(stock_id, date, summary_data, timeout: int = SUMMARY_CACHE_TIMEOUT) -> None:
    key = summary_cache_key(stock_id, date)
    try:
        cache.set(key, summary_data, timeout=timeout)
    except Exception:
        logger.warning("[summary_cache] set failed key=%s", key, exc_info=True)


def get_cached_summaries(keys_by_stock_id: dict) -> dict:
    """keys_by_stock_id: {stock_id: cache_key}. Redis 장애 시 빈 dict를 반환해 전부 캐시 미스로 처리한다."""
    if not keys_by_stock_id:
        return {}

    try:
        raw = cache.get_many(list(keys_by_stock_id.values()))
    except Exception:
        logger.warning("[summary_cache] get_many failed", exc_info=True)
        return {}

    key_to_stock_id = {key: stock_id for stock_id, key in keys_by_stock_id.items()}
    return {
        key_to_stock_id[key]: value
        for key, value in raw.items()
        if key in key_to_stock_id
    }


def set_cached_summaries(items, timeout: int = SUMMARY_CACHE_TIMEOUT) -> None:
    """items: (stock_id, date, summary_data) 튜플들의 iterable."""
    mapping = {
        summary_cache_key(stock_id, date): summary_data
        for stock_id, date, summary_data in items
    }
    if not mapping:
        return

    try:
        cache.set_many(mapping, timeout=timeout)
    except Exception:
        logger.warning("[summary_cache] set_many failed", exc_info=True)
