import os
import time
import random
import asyncio
import threading
from enum import Enum
from typing import Optional, Dict, Tuple, Any, Callable
from collections import deque
from dotenv import load_dotenv

from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout_ms: int = 60000):
        self.failure_threshold = failure_threshold
        self.timeout_ms = timeout_ms
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
        self.lock = threading.Lock()

    def execute(self, operation: Callable):
        with self.lock:
            if self.state == CircuitState.OPEN:
                if (
                    self.last_failure_time
                    and (time.time() * 1000 - self.last_failure_time) > self.timeout_ms
                ):
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise Exception("Circuit breaker is OPEN")

        try:
            result = operation()
            self._on_success()
            return result
        except Exception as error:
            self._on_failure()
            raise error

    def _on_success(self):
        with self.lock:
            self.failure_count = 0
            self.state = CircuitState.CLOSED

    def _on_failure(self):
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time() * 1000
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN


class RateLimiter:
    def __init__(self, max_requests: int = 10, window_ms: int = 60000):
        self.max_requests = max_requests
        self.window_ms = window_ms
        self.requests = deque()
        self.lock = threading.Lock()

    async def wait_for_slot(self):
        with self.lock:
            now = time.time() * 1000
            while self.requests and now - self.requests[0] >= self.window_ms:
                self.requests.popleft()

            if len(self.requests) >= self.max_requests:
                if self.requests:
                    oldest_request = self.requests[0]
                    wait_time = self.window_ms - (now - oldest_request) + 100
                    await asyncio.sleep(wait_time / 1000)
                    return await self.wait_for_slot()

            self.requests.append(now)


circuit_breaker = CircuitBreaker(3, 60000)
rate_limiter = RateLimiter(5, 30000)


def get_supabase() -> Client:
    return supabase


async def load_subscriptions() -> Dict[str, Dict]:
    from redis_cache import (
        get_all_subscriptions_cached,
        cache_all_subscriptions,
        cache_hits,
        cache_misses,
    )

    cached = get_all_subscriptions_cached()
    if cached is not None:
        return cached

    await rate_limiter.wait_for_slot()

    try:
        response = circuit_breaker.execute(
            lambda: supabase.table("subscriptions").select("*").execute()
        )

        if not response or response.data is None:
            print("[WARNING] No data returned - Check RLS policies or table access")
            return {}

        if not response.data:
            return {}

        subscriptions = {}
        for row in response.data:
            subscriptions[row["userId"]] = {"subscribed": bool(row["subscribed"])}

        cache_all_subscriptions(subscriptions)
        return subscriptions

    except Exception as e:
        error_str = str(e)
        if "no RLS policies" in error_str or "no data will be returned" in error_str:
            print("[ERROR] RLS policies issue detected - table access blocked")
        return {}


async def subscribe_user(user_id: str) -> Tuple[bool, str]:
    from redis_cache import (
        get_cached_user_data,
        set_cached_user_data,
        update_user_subscription_in_cache,
    )

    try:
        cached_data = get_cached_user_data(user_id)
        if cached_data:
            subscriptions = {user_id: cached_data}
        else:
            subscriptions = await load_subscriptions()

        if user_id not in subscriptions:
            subscription_data = {"subscribed": True}
        else:
            subscription_data = subscriptions[user_id].copy()
            subscription_data["subscribed"] = True

        await rate_limiter.wait_for_slot()

        result = circuit_breaker.execute(
            lambda: (
                supabase.table("subscriptions")
                .upsert({"userId": user_id, "subscribed": True})
                .execute()
            )
        )

        set_cached_user_data(user_id, subscription_data)
        update_user_subscription_in_cache(user_id, True)
        return True, "Successfully subscribed to YC News updates"

    except Exception as e:
        error_str = str(e)
        if "row-level security" in error_str or "no RLS policies" in error_str:
            return (
                False,
                "Database permission error. You need to either: 1) Add RLS policies, or 2) Disable RLS completely in Supabase.",
            )
        elif "duplicate key" in error_str:
            return True, "Already subscribed to YC News updates"
        else:
            return False, f"Error processing subscription: {error_str}"


async def unsubscribe_user(user_id: str) -> Tuple[bool, str]:
    from redis_cache import (
        get_cached_user_data,
        set_cached_user_data,
        update_user_subscription_in_cache,
    )

    try:
        cached_data = get_cached_user_data(user_id)
        if cached_data:
            subscriptions = {user_id: cached_data}
        else:
            subscriptions = await load_subscriptions()

        if user_id in subscriptions:
            subscriptions[user_id]["subscribed"] = False

        await rate_limiter.wait_for_slot()
        result = circuit_breaker.execute(
            lambda: (
                supabase.table("subscriptions")
                .upsert({"userId": user_id, "subscribed": False})
                .execute()
            )
        )

        if result and result.data:
            if user_id in subscriptions:
                subscriptions[user_id]["subscribed"] = False
                set_cached_user_data(user_id, subscriptions[user_id])
            update_user_subscription_in_cache(user_id, False)
            return True, "Successfully unsubscribed from YC News updates"
        else:
            return False, "Failed to update subscription record"

    except Exception as e:
        error_str = str(e)
        if "row-level security" in error_str:
            return (
                False,
                "Database permission error. Please check RLS policies in Supabase.",
            )
        else:
            return False, f"Error processing unsubscription: {error_str}"
