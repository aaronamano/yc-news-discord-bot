import os
import asyncio
import requests
from bs4 import BeautifulSoup
import discord
from discord.ext import tasks
from dotenv import load_dotenv
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
HN_URL = "https://news.ycombinator.com/newest"

intents = discord.Intents.default()
client = discord.Client(intents=intents)

posted_ids = set()

def fetch_newest(top_n=15):
    resp = requests.get(HN_URL, timeout=10)
    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    rows = soup.select("tr.athing")[:top_n]
    for row in rows:
        item_id = row.get("id")
        title_link = row.select_one("span.titleline a")
        title = title_link.text if title_link else "No title"
        url = title_link["href"] if title_link else "#"
        hn_link = f"https://news.ycombinator.com/item?id={item_id}"

        subtext = row.find_next_sibling("tr").select_one("td.subtext")
        age = subtext.select_one("span.age").text if subtext else "unknown"

        items.append({
            "id": item_id,
            "title": title,
            "url": url,
            "hn_link": hn_link,
            "age": age,
        })
    return items

@tasks.loop(hours=1)
async def poll_hn():
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        return

    items = fetch_newest()
    new_items = [it for it in items if it["id"] not in posted_ids]

    # Optional: only post first N new items each cycle
    for item in reversed(new_items):  # reversed to post oldest new first
        posted_ids.add(item["id"])
        msg = f"{item['hn_link']}"
        await channel.send(msg)
        await asyncio.sleep(10)  # tiny delay to avoid rate limits

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    poll_hn.start()

client.run(DISCORD_TOKEN)
