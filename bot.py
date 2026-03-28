import os
import asyncio
import discord
from discord.ext import tasks
from dotenv import load_dotenv
import time
import random
import requests

from redis_cache import (
    init_redis,
    delete_cached_user_data,
    cleanup_expired_cache,
    get_cache_stats,
    cache_lock,
    user_cache,
    cache_expiry,
)

from supabase_client import load_subscriptions, subscribe_user, unsubscribe_user

from rate_limiter import (
    exponential_backoff,
    wait_for_rate_limit,
    dm_cooldowns,
    MAX_RETRIES,
)

from metadata_cache import (
    get_cached_timezone_names,
    get_cached_extension_info,
    get_cached_function_metadata,
)

from hn_scraper import fetch_hn_stories, debug_hn_scraping

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
if not DISCORD_TOKEN or not CHANNEL_ID:
    exit(1)

init_redis()

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.dm_messages = True
client = discord.Client(intents=intents)

posted_ids = set()
connection_attempts = 0
last_connection_attempt = 0


async def send_dm_to_user(user, story):
    user_id = str(user.id)

    current_time = time.time()
    if user_id in dm_cooldowns and current_time - dm_cooldowns[user_id] < 1.0:
        return False

    try:
        await wait_for_rate_limit("dm")

        source_link = (
            story["hn_link"] if story["url"].startswith("item?id=") else story["url"]
        )

        embed = discord.Embed(
            title=story["title"][:256],
            description=f"📰 **Source**: {source_link} | ⏰ **Age**: {story['age']}",
            url=source_link,
        )

        await user.send(embed=embed)

        dm_cooldowns[user_id] = current_time

        return True
    except discord.Forbidden:
        return False
    except discord.HTTPException as e:
        if e.status == 429:
            retry_after = e.response.headers.get("Retry-After")
            if retry_after:
                await asyncio.sleep(int(retry_after) + 1)
        return False
    except Exception:
        return False


@tasks.loop(hours=20)
async def send_news_dms():
    try:
        subscriptions = await load_subscriptions()
        if not subscriptions:
            return

        stories = fetch_hn_stories()
        if not stories:
            return

        new_stories = [s for s in stories[:20] if s["id"] not in posted_ids]

        if not new_stories:
            return

        processed_users = 0
        for user_id, user_data in subscriptions.items():
            if processed_users >= 5:
                break

            if not user_data.get("subscribed"):
                continue

            stories_to_send = new_stories[:3]

            if stories_to_send:
                try:
                    user = await client.fetch_user(int(user_id))
                    if user:
                        for i, story in enumerate(stories_to_send):
                            if await send_dm_to_user(user, story):
                                if i < len(stories_to_send) - 1:
                                    await asyncio.sleep(2)
                        processed_users += 1
                    else:
                        break
                except Exception:
                    continue

        for story in new_stories:
            posted_ids.add(story["id"])

    except Exception as e:
        print(f"[ERROR] Error in send_news_dms: {e}")
        return


@tasks.loop(minutes=10)
async def cleanup_cache_task():
    cleanup_expired_cache()


@tasks.loop(hours=1)
async def cache_stats_task():
    stats = get_cache_stats()
    print(
        f"[CACHE STATS] Hits: {stats['cache_hits']}, Misses: {stats['cache_misses']}, Hit Ratio: {stats['hit_ratio']:.2%}, Memory Size: {stats['memory_cache_size']}, Redis Connected: {stats['redis_connected']}"
    )


async def preload_critical_caches():
    try:
        await asyncio.gather(
            get_cached_timezone_names(),
            get_cached_extension_info(),
            get_cached_function_metadata(),
        )
        print("[INFO] Critical metadata caches preloaded successfully")
    except Exception as e:
        print(f"[WARNING] Failed to preload caches: {e}")


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.channel.id != CHANNEL_ID:
        return

    content = message.content.strip()

    if content.startswith("!yc-news subscribe"):
        try:
            user_id = str(message.author.id)

            success, response_message = await subscribe_user(user_id)

            if success:
                await message.author.send(f"✅ {response_message}")
            else:
                await message.channel.send(f"❌ {response_message}")
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(2)
            await message.channel.send(
                "❌ Error processing subscription. Please try again later."
            )
        except Exception:
            await message.channel.send(
                "❌ Error processing subscription. Please try again."
            )

    elif content.startswith("!yc-news unsubscribe"):
        try:
            user_id = str(message.author.id)

            success, response_message = await unsubscribe_user(user_id)

            if success:
                await message.author.send(f"❌ {response_message}")
            else:
                await message.channel.send(f"❌ {response_message}")
        except Exception:
            await message.channel.send(
                "❌ Error processing unsubscription. Please try again."
            )

    elif content == "!yc-news clear":
        global posted_ids
        posted_ids.clear()
        await message.author.send(
            "🗑️ Posted story cache cleared. Stories can be resent now."
        )

    elif content == "!yc-news cache-stats":
        stats = get_cache_stats()
        msg = f"""📊 **Cache Performance Stats**
        
**Cache Performance:**
• Hits: {stats["cache_hits"]}
• Misses: {stats["cache_misses"]}
• Hit Ratio: {stats["hit_ratio"]:.2%}
• Memory Cache Size: {stats["memory_cache_size"]} entries
• Redis Connected: {"✅ Yes" if stats["redis_connected"] else "❌ No (Memory fallback)"}

**Cache TTL Settings:**
• User Subscriptions: 5 minutes
• Timezone Names: 24 hours
• Extension Info: 12 hours
• Function Metadata: 12 hours"""
        await message.author.send(msg)

    elif content == "!yc-news refresh-cache":
        user_id = str(message.author.id)

        with cache_lock:
            keys_to_remove = [
                k for k in user_cache.keys() if k.startswith(f"user_sub:{user_id}")
            ]
            for key in keys_to_remove:
                user_cache.pop(key, None)
                cache_expiry.pop(key, None)

        delete_cached_user_data(user_id)

        await preload_critical_caches()

        await message.author.send(
            "🔄 Your cache has been refreshed and critical caches preloaded."
        )

    elif content == "!yc-news preload":
        await preload_critical_caches()

        stats = get_cache_stats()
        await message.author.send(f"""⚡ **Critical Caches Preloaded**
        
✅ Timezone Names (24h TTL)
✅ Extension Info (12h TTL)  
✅ Function Metadata (12h TTL)

Cache Status: {stats["hit_ratio"]:.2%} hit ratio
Redis Status: {"Connected" if stats["redis_connected"] else "Memory Fallback"}""")

    elif content == "!yc-news test":
        debug_info = await debug_hn_scraping()

        msg_parts = []
        msg_parts.append(f"🔍 **HN Scraping Debug Report**")
        msg_parts.append(f"**Status:** {debug_info['status']}")

        if "network" in debug_info["steps"]:
            msg_parts.append(f"**Network:** {debug_info['steps']['network']}")
        if "parsing" in debug_info["steps"]:
            msg_parts.append(f"**HTML Parsing:** {debug_info['steps']['parsing']}")

        if "selectors" in debug_info["steps"]:
            msg_parts.append("**CSS Selectors:**")
            for selector, result in debug_info["steps"]["selectors"].items():
                msg_parts.append(f"  • `{selector}` → {result}")

        if "parsing_analysis" in debug_info["steps"]:
            analysis = debug_info["steps"]["parsing_analysis"]
            msg_parts.append(
                f"**Analysis:** {analysis['total_rows']} total rows, limited to {analysis['limited_to']}"
            )

        if "parsing_results" in debug_info["steps"]:
            results = debug_info["steps"]["parsing_results"]
            msg_parts.append(
                f"**Parsing Results:** {results['parsed_count']} successful, {results['failed_count']} failed"
            )

            if results["failure_reasons"]:
                msg_parts.append("**Failure Reasons:**")
                for reason, count in results["failure_reasons"].items():
                    msg_parts.append(f"  • {reason}: {count}")

        if "final_result" in debug_info["steps"]:
            msg_parts.append(f"**Final Result:** {debug_info['steps']['final_result']}")

        if debug_info["sample_stories"]:
            msg_parts.append("\n**Sample Stories Analysis:**")
            for story in debug_info["sample_stories"][:3]:
                msg_parts.append(f"\n**Story {story['index']}:**")
                msg_parts.append(f"  • ID: {story['steps'].get('id', 'N/A')}")
                if "title" in story:
                    msg_parts.append(f"  • Title: {story['title'][:60]}...")
                if "url" in story:
                    msg_parts.append(f"  • URL: {story['url'][:40]}...")
                msg_parts.append(
                    f"  • Title Link: {story['steps'].get('title_link', 'N/A')}"
                )
                if "age" in story["steps"]:
                    msg_parts.append(f"  • Age: {story['steps']['age']}")

        if debug_info["errors"]:
            msg_parts.append("\n**Errors:**")
            for error in debug_info["errors"]:
                msg_parts.append(f"  • {error}")

        debug_message = "\n".join(msg_parts)

        if len(debug_message) > 1900:
            parts = [
                debug_message[i : i + 1900] for i in range(0, len(debug_message), 1900)
            ]
            for i, part in enumerate(parts):
                header = (
                    f"🔍 **HN Scraping Debug Report (Part {i + 1}/{len(parts)})**"
                    if i > 0
                    else part
                )
                await message.author.send(header)
                await asyncio.sleep(0.5)
        else:
            await message.author.send(debug_message)


async def run_bot_with_retry():
    global connection_attempts, last_connection_attempt

    for attempt in range(MAX_RETRIES):
        try:
            connection_attempts = attempt + 1
            last_connection_attempt = time.time()

            if attempt > 0:
                delay = exponential_backoff(attempt)
                print(
                    f"[INFO] Waiting {delay}s before retry {attempt + 1}/{MAX_RETRIES}"
                )
                await asyncio.sleep(delay)

            await client.login(DISCORD_TOKEN)
            await client.connect()

            print(f"[INFO] Bot connected successfully on attempt {attempt + 1}")
            break

        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = (
                    e.response.headers.get("Retry-After") if e.response else None
                )
                if retry_after:
                    wait_time = int(retry_after) + random.randint(1, 5)
                    print(f"[INFO] Rate limited. Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                else:
                    wait_time = exponential_backoff(attempt)
                    print(f"[INFO] Rate limited. Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
            elif "HTML" in str(e) or "doctype" in str(e).lower():
                wait_time = exponential_backoff(attempt) * 2
                print(f"[INFO] Possible Cloudflare protection. Waiting {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                print(f"[ERROR] Discord HTTP error: {e}")
                if attempt == MAX_RETRIES - 1:
                    raise

        except Exception as e:
            print(f"[ERROR] Connection attempt {attempt + 1} failed: {e}")
            if attempt == MAX_RETRIES - 1:
                raise
            await asyncio.sleep(exponential_backoff(attempt))


@client.event
async def on_ready():
    print(f"[INFO] Bot is ready! Logged in as {client.user}")

    await preload_critical_caches()

    await get_cached_timezone_names()
    await get_cached_extension_info()
    await get_cached_function_metadata()

    send_news_dms.start()
    cleanup_cache_task.start()
    cache_stats_task.start()

    print(
        "[INFO] Background tasks started: news delivery, cache cleanup, cache statistics"
    )


@client.event
async def on_disconnect():
    print("[INFO] Bot disconnected")


if __name__ == "__main__":
    if hasattr(requests, "Session"):
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": "Mozilla/5.0 (compatible; YCNewsBot/1.0; +https://github.com/yc-news-bot)"
            }
        )

    asyncio.run(run_bot_with_retry())
