import asyncio
import random
import time

BASE_RETRY_DELAY = 2
MAX_RETRY_DELAY = 300
MAX_RETRIES = 5
API_RATE_LIMIT = 45
DM_RATE_LIMIT = 5

last_api_request = {}
dm_cooldowns = {}


def exponential_backoff(attempt: int) -> int:
    delay = min(BASE_RETRY_DELAY * (2**attempt), MAX_RETRY_DELAY)
    jitter = random.uniform(0.1, 0.5) * delay
    return int(delay + jitter)


async def rate_limit_check(operation_type: str = "api") -> bool:
    global last_api_request, dm_cooldowns

    current_time = time.time()

    if operation_type == "api":
        last_minute_requests = [
            t for t in last_api_request.get("api", []) if current_time - t < 60
        ]
        if len(last_minute_requests) >= API_RATE_LIMIT:
            return False

        if "api" not in last_api_request:
            last_api_request["api"] = []
        last_api_request["api"].append(current_time)
        last_api_request["api"] = [
            t for t in last_api_request["api"] if current_time - t < 60
        ]

    elif operation_type == "dm":
        if len(dm_cooldowns) >= DM_RATE_LIMIT:
            return False

    return True


async def wait_for_rate_limit(operation_type: str = "api"):
    while not await rate_limit_check(operation_type):
        if operation_type == "api":
            await asyncio.sleep(1.5)
        else:
            await asyncio.sleep(0.6)
