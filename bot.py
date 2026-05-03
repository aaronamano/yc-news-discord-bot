import os
import asyncio
import discord
from discord.ext import tasks
from dotenv import load_dotenv
import time
import random
import requests

from rate_limiter import (
    exponential_backoff,
    wait_for_rate_limit,
    MAX_RETRIES,
)

from hn_scraper import fetch_hn_stories

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
if not DISCORD_TOKEN or not CHANNEL_ID:
    exit(1)

intents = discord.Intents.default()
intents.guilds = True
client = discord.Client(intents=intents)

posted_ids = set()
connection_attempts = 0
last_connection_attempt = 0


async def send_story_to_channel(channel, story):
    try:
        await wait_for_rate_limit("channel")

        source_link = (
            story["hn_link"] if story["url"].startswith("item?id=") else story["url"]
        )

        embed = discord.Embed(
            title=story["title"][:256],
            description=f"📰 **Source**: {source_link} | ⏰ **Age**: {story['age']}",
            url=source_link,
        )

        await channel.send(embed=embed)
        return True
    except discord.HTTPException as e:
        if e.status == 429:
            retry_after = e.response.headers.get("Retry-After")
            if retry_after:
                await asyncio.sleep(int(retry_after) + 1)
        return False
    except Exception:
        return False


@tasks.loop(hours=20)
async def post_news_to_channel():
    try:
        channel = client.get_channel(CHANNEL_ID)
        if not channel:
            return

        stories = fetch_hn_stories()
        if not stories:
            return

        new_stories = [s for s in stories[:20] if s["id"] not in posted_ids]

        if not new_stories:
            return

        for i, story in enumerate(new_stories):
            if await send_story_to_channel(channel, story):
                if i < len(new_stories) - 1:
                    await asyncio.sleep(2)

        for story in new_stories:
            posted_ids.add(story["id"])

    except Exception as e:
        print(f"[ERROR] Error in post_news_to_channel: {e}")
        return


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

    post_news_to_channel.start()

    print("[INFO] Background task started: post news to channel")


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
