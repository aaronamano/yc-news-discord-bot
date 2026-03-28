import os
import json
import time
import threading
from typing import Optional, Any, Dict
from dotenv import load_dotenv

load_dotenv()

try:
    import redis

    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    print("[WARNING] Redis not available - caching will be in-memory only")

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

METADATA_CACHE_TTL = {
    "timezone_names": 86400,
    "extension_info": 43200,
    "function_metadata": 43200,
    "user_subscriptions": 300,
}
CACHE_TTL = 300

redis_client = None

cache_lock = threading.Lock()
cache_hits = 0
cache_misses = 0
user_cache: Dict[str, Any] = {}
cache_expiry: Dict[str, float] = {}


def init_redis() -> Optional[Any]:
    global redis_client
    if not REDIS_AVAILABLE:
        return None
    try:
        client = redis.from_url(REDIS_URL, decode_responses=True)
        client.ping()
        redis_client = client
        print("[INFO] Redis connected successfully")
        return client
    except Exception as e:
        print(f"[WARNING] Redis connection failed: {e} - using memory cache")
        return None


def get_redis() -> Optional[Any]:
    return redis_client


def get_cached_data(cache_key: str, cache_type: str = "default") -> Optional[Any]:
    global cache_hits, cache_misses

    ttl = METADATA_CACHE_TTL.get(cache_type, CACHE_TTL)

    if REDIS_AVAILABLE and redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                cache_hits += 1
                return json.loads(cached)
        except Exception as e:
            print(f"[WARNING] Redis get failed: {e} - falling back to memory")

    with cache_lock:
        if cache_key in user_cache and cache_key in cache_expiry:
            if time.time() - cache_expiry[cache_key] < ttl:
                cache_hits += 1
                return user_cache[cache_key]
            else:
                user_cache.pop(cache_key, None)
                cache_expiry.pop(cache_key, None)

    cache_misses += 1
    return None


def set_cached_data(cache_key: str, data: Any, cache_type: str = "default"):
    ttl = METADATA_CACHE_TTL.get(cache_type, CACHE_TTL)

    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.setex(cache_key, ttl, json.dumps(data))
        except (TypeError, ValueError) as e:
            print(f"[WARNING] Redis set failed (not JSON serializable): {e}")
        except Exception as e:
            print(f"[WARNING] Redis set failed: {e}")

    with cache_lock:
        user_cache[cache_key] = data
        cache_expiry[cache_key] = time.time()


def get_cached_user_data(user_id: str) -> Optional[Dict]:
    cache_key = f"user_sub:{user_id}"
    return get_cached_data(cache_key, "user_subscriptions")


def set_cached_user_data(user_id: str, data: Dict):
    cache_key = f"user_sub:{user_id}"
    set_cached_data(cache_key, data, "user_subscriptions")


def delete_cached_user_data(user_id: str):
    cache_key = f"user_sub:{user_id}"

    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.delete(cache_key)
        except Exception as e:
            print(f"[WARNING] Redis delete failed: {e}")

    with cache_lock:
        user_cache.pop(cache_key, None)
        cache_expiry.pop(cache_key, None)


def cache_all_subscriptions(subscriptions: Dict[str, Dict]) -> None:
    if REDIS_AVAILABLE and redis_client:
        try:
            pipeline = redis_client.pipeline()
            pipeline.delete("subscriptions:all")

            if subscriptions:
                pipeline.hset(
                    "subscriptions:all",
                    mapping={
                        user_id: json.dumps(data)
                        for user_id, data in subscriptions.items()
                    },
                )
                pipeline.expire(
                    "subscriptions:all", METADATA_CACHE_TTL["user_subscriptions"]
                )

            pipeline.execute()
        except Exception as e:
            print(f"[WARNING] Redis bulk cache failed: {e}")

    with cache_lock:
        user_cache["subscriptions:all"] = subscriptions
        cache_expiry["subscriptions:all"] = time.time()


def get_all_subscriptions_cached() -> Optional[Dict[str, Dict]]:
    if REDIS_AVAILABLE and redis_client:
        try:
            cached = redis_client.hgetall("subscriptions:all")
            if cached:
                global cache_hits
                cache_hits += 1
                return {user_id: json.loads(data) for user_id, data in cached.items()}
        except Exception as e:
            print(f"[WARNING] Redis hgetall failed: {e}")

    cache_key = "subscriptions:all"
    with cache_lock:
        if cache_key in user_cache and cache_key in cache_expiry:
            if (
                time.time() - cache_expiry[cache_key]
                < METADATA_CACHE_TTL["user_subscriptions"]
            ):
                return user_cache[cache_key]

    return None


def update_user_subscription_in_cache(user_id: str, subscribed: bool) -> None:
    if REDIS_AVAILABLE and redis_client:
        try:
            data = json.dumps({"subscribed": subscribed})
            redis_client.hset("subscriptions:all", user_id, data)
        except Exception as e:
            print(f"[WARNING] Redis update failed: {e}")

    with cache_lock:
        if "subscriptions:all" not in user_cache:
            user_cache["subscriptions:all"] = {}
        user_cache["subscriptions:all"][user_id] = {"subscribed": subscribed}


def cleanup_expired_cache():
    with cache_lock:
        current_time = time.time()
        expired_keys = [
            key
            for key, expiry_time in cache_expiry.items()
            if current_time - expiry_time
            >= METADATA_CACHE_TTL.get("default", CACHE_TTL)
        ]
        for key in expired_keys:
            user_cache.pop(key, None)
            cache_expiry.pop(key, None)

        if expired_keys:
            print(f"[INFO] Cleaned {len(expired_keys)} expired cache entries")


def get_cache_stats() -> Dict:
    total_requests = cache_hits + cache_misses
    hit_ratio = cache_hits / total_requests if total_requests > 0 else 0

    return {
        "cache_hits": cache_hits,
        "cache_misses": cache_misses,
        "hit_ratio": hit_ratio,
        "memory_cache_size": len(user_cache),
        "redis_connected": REDIS_AVAILABLE and redis_client is not None,
    }
