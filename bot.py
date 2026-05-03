import os
import discord
from discord.ext import tasks
from dotenv import load_dotenv
import asyncio
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


async def send_story_to_channel(channel, story):
    try:
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


@client.event
async def on_ready():
    print(f"[INFO] Bot is ready! Logged in as {client.user}")

    post_news_to_channel.start()

    print("[INFO] Background task started: post news to channel")


@client.event
async def on_disconnect():
    print("[INFO] Bot disconnected")


if __name__ == "__main__":
    client.run(DISCORD_TOKEN)
