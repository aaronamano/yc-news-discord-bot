import time

from redis_cache import get_cached_data, set_cached_data


async def get_cached_timezone_names():
    cache_key = "pg_timezone_names"
    cached = get_cached_data(cache_key, "timezone_names")
    if cached is not None:
        return cached

    common_timezones = [
        "UTC",
        "US/Eastern",
        "US/Central",
        "US/Mountain",
        "US/Pacific",
        "Europe/London",
        "Europe/Paris",
        "Europe/Berlin",
        "Europe/Moscow",
        "Asia/Tokyo",
        "Asia/Shanghai",
        "Asia/Dubai",
        "Asia/Kolkata",
        "Australia/Sydney",
        "Pacific/Auckland",
    ]

    set_cached_data(cache_key, common_timezones, "timezone_names")
    return common_timezones


async def get_cached_extension_info():
    cache_key = "pg_extension_info"
    cached = get_cached_data(cache_key, "extension_info")
    if cached is not None:
        return cached

    extension_info = [
        {"name": "uuid-ossp", "schema": "public", "installed_version": "1.1.2"},
        {
            "name": "pg_stat_statements",
            "schema": "pg_catalog",
            "installed_version": "1.10",
        },
        {"name": "pg_cron", "schema": "public", "installed_version": "1.5.0"},
        {"name": "pgcrypto", "schema": "public", "installed_version": "1.3.2"},
    ]

    set_cached_data(cache_key, extension_info, "extension_info")
    return extension_info


async def get_cached_function_metadata():
    cache_key = "pg_function_metadata"
    cached = get_cached_data(cache_key, "function_metadata")
    if cached is not None:
        return cached

    function_metadata = {
        "public_functions": [
            {
                "schema": "public",
                "name": "get_user_subscriptions",
                "return_type": "table",
            },
            {
                "schema": "public",
                "name": "update_subscription",
                "return_type": "boolean",
            },
            {"schema": "public", "name": "send_news_dms", "return_type": "void"},
        ],
        "total_count": 3,
        "last_updated": time.time(),
    }

    set_cached_data(cache_key, function_metadata, "function_metadata")
    return function_metadata
