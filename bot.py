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

def fetch_meta_data(url):
    """Fetch meta description and image from a URL"""
    try:
        # Skip internal HN links
        if url.startswith("item?id="):
            return None, None
        
        # Ensure URL is absolute
        if not url.startswith(("http://", "https://")):
            url = "https://news.ycombinator.com/" + url
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        
        response = requests.get(url, timeout=10, headers=headers)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.text, "html.parser")
        
        # Try to get meta description
        description = None
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            description = meta_desc.get("content").strip()
        else:
            # Fallback to og:description
            og_desc = soup.find("meta", attrs={"property": "og:description"})
            if og_desc and og_desc.get("content"):
                description = og_desc.get("content").strip()
        
        # Try to get meta image
        image_url = None
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image and og_image.get("content"):
            image_url = og_image.get("content").strip()
            # Convert relative URLs to absolute
            if image_url.startswith("//"):
                image_url = "https:" + image_url
            elif image_url.startswith("/"):
                from urllib.parse import urljoin
                image_url = urljoin(url, image_url)
            elif not image_url.startswith(("http://", "https://")):
                from urllib.parse import urljoin
                image_url = urljoin(url, image_url)
        
        return description, image_url
        
    except Exception as e:
        print(f"Error fetching meta data for {url}: {e}")
        return None, None

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

@tasks.loop(minutes=30)
async def poll_hn():
    channel = client.get_channel(CHANNEL_ID)
    if channel is None:
        return

    items = fetch_newest()
    new_items = [it for it in items if it["id"] not in posted_ids]

    # Optional: only post first N new items each cycle
    for item in reversed(new_items):  # reversed to post oldest new first
        posted_ids.add(item["id"])

        # Fetch meta description and image for the URL
        description, image_url = fetch_meta_data(item['url'])
        
        # Create embed with title only (no URL)
        embed = discord.Embed(
            title=item['title'],
            description=description if description else ""
        )
        
        # Add image if available
        if image_url:
            embed.set_image(url=image_url)
        
        # Add source link and HN discussion link in the description
        source_link = item['hn_link'] if item['url'].startswith("item?id=") else item['url']
        current_desc = embed.description or ""
        
        # Add clickable source link
        embed.description = current_desc + f"\n\n{source_link}"
        
        await channel.send(embed=embed)
        await asyncio.sleep(1)  # tiny delay to avoid rate limits

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    poll_hn.start()

client.run(DISCORD_TOKEN)
