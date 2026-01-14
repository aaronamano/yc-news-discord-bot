import os
import asyncio
import requests
import json
import re
import time
from bs4 import BeautifulSoup
import discord
from discord.ext import tasks, commands
from dotenv import load_dotenv
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

posted_ids = set()
SUBSCRIPTIONS_FILE = 'subscriptions.json'

def load_subscriptions():
    try:
        with open(SUBSCRIPTIONS_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_subscriptions(subscriptions):
    with open(SUBSCRIPTIONS_FILE, 'w') as f:
        json.dump(subscriptions, f, indent=2)

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

def fetch_newest(top_n=15, tags=None):
    # API endpoint that powers your requested URL: 
    # https://hn.algolia.com/?dateRange=last24h&page=0&prefix=true&sort=byDate&type=story
    api_url = "https://hn.algolia.com/api/v1/search"
    
    # Parameters that match your URL requirements
    params = {
        'tags': 'story',  # type=story
        'hitsPerPage': top_n
    }
    
    # Handle dateRange=last24h
    twenty_four_hours_ago = int(time.time()) - 86400
    params['numericFilters'] = f'created_at_i>{twenty_four_hours_ago}'
    
    # Handle query tags - integrates tags with URL as specified
    # When users add tags like "AI, ML, LLMs", each word is parsed and added to the URL
    # This corresponds to &query=AI&query=ML&query=LLMs in the base URL
    # For the API, we combine them into a single query string
    if tags:
        params['query'] = ' '.join(tags)
    
    try:
        resp = requests.get(api_url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        items = []
        for hit in data.get('hits', []):
            title = hit.get('title', 'No title')
            story_url = hit.get('url', '')
            if not story_url:
                story_url = hit.get('story_url', '')
            
            object_id = hit.get('objectID', str(len(items)))
            hn_link = f"https://news.ycombinator.com/item?id={object_id}"
            
            # Convert timestamp to readable age
            created_at = hit.get('created_at', 'recent')
            
            items.append({
                "id": object_id,
                "title": title,
                "url": story_url,
                "hn_link": hn_link,
                "age": created_at,
            })
        
        return items
        
    except Exception as e:
        print(f"Error fetching from Algolia: {e}")
        return []

@tasks.loop(hours=2)
async def poll_hn():
    try:
        subscriptions = load_subscriptions()
        
        for user_id, user_data in subscriptions.items():
            if not user_data.get('subscribed', False):
                continue
                
            tags = user_data.get('tags', [])
            items = fetch_newest(15, tags)
            new_items = [it for it in items if it["id"] not in posted_ids]

            if not new_items:
                continue

            user = await client.fetch_user(int(user_id))
            if not user:
                continue

            for item in reversed(new_items[:5]):  # Limit to 5 items per user per hour
                posted_ids.add(item["id"])

                description, image_url = fetch_meta_data(item["url"])
                if description:
                    description = description[:1800]

                source_link = item["hn_link"] if item["url"].startswith("item?id=") else item["url"]
                base_desc = description or ""
                extra = f"\n\n{source_link}"

                full_desc = (base_desc + extra)
                if len(full_desc) > 4000:
                    full_desc = full_desc[:4000]

                embed = discord.Embed(
                    title=item["title"][:256],
                    description=full_desc
                )

                if image_url:
                    embed.set_image(url=image_url)

                try:
                    await user.send(embed=embed)
                    await asyncio.sleep(1)
                except discord.Forbidden:
                    print(f"Cannot send DM to user {user_id}")
                    
    except Exception as e:
        print(f"Error in poll_hn loop: {e}")




@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    poll_hn.start()
    print("Bot is running. Press Ctrl+C to stop.")

@client.event
async def on_message(message):
    if message.author == client.user:
        return
    
    # Only allow commands in the specified channel
    if message.channel.id != CHANNEL_ID:
        return
    
    content = message.content.strip()
    
    if content.startswith('!yc-news subscribe'):
        subscriptions = load_subscriptions()
        user_id = str(message.author.id)
        
        if user_id not in subscriptions:
            subscriptions[user_id] = {'subscribed': True, 'tags': []}
        else:
            subscriptions[user_id]['subscribed'] = True
        
        save_subscriptions(subscriptions)
        await message.author.send("✅ You have been subscribed to YC News updates!")
        await message.channel.send(f"{message.author.mention} subscribed")
        
    elif content.startswith('!yc-news unsubscribe'):
        subscriptions = load_subscriptions()
        user_id = str(message.author.id)
        
        if user_id in subscriptions:
            subscriptions[user_id]['subscribed'] = False
        
        save_subscriptions(subscriptions)
        await message.author.send("❌ You have been unsubscribed from YC News updates.")
        await message.channel.send(f"{message.author.mention} unsubscribed")
        
    elif content.startswith('!yc-news add='):
        subscriptions = load_subscriptions()
        user_id = str(message.author.id)
        
        if user_id not in subscriptions:
            subscriptions[user_id] = {'subscribed': True, 'tags': []}
        
        tags_str = content.split('=', 1)[1].strip()
        if tags_str.startswith('"') and tags_str.endswith('"'):
            tags_str = tags_str[1:-1]
        
        new_tags = [tag.strip() for tag in tags_str.split(',') if tag.strip()]
        
        if user_id not in subscriptions:
            subscriptions[user_id] = {'subscribed': True, 'tags': []}
        if 'tags' not in subscriptions[user_id]:
            subscriptions[user_id]['tags'] = []
            
        for tag in new_tags:
            if tag not in subscriptions[user_id]['tags']:
                subscriptions[user_id]['tags'].append(tag)
        
        save_subscriptions(subscriptions)
        await message.author.send(f"✅ You added {', '.join(new_tags)}")
        await message.channel.send(f"{message.author.mention} added \"{', '.join(new_tags)}\"")
        
    elif content.startswith('!yc-news remove='):
        subscriptions = load_subscriptions()
        user_id = str(message.author.id)
        
        if user_id not in subscriptions:
            await message.author.send("❌ You are not subscribed yet.")
            return
        
        tags_str = content.split('=', 1)[1].strip()
        if tags_str.startswith('"') and tags_str.endswith('"'):
            tags_str = tags_str[1:-1]
        
        tags_to_remove = [tag.strip() for tag in tags_str.split(',') if tag.strip()]
        
        if 'tags' not in subscriptions[user_id]:
            subscriptions[user_id]['tags'] = []
            
        removed_tags = []
        for tag in tags_to_remove:
            if tag in subscriptions[user_id]['tags']:
                subscriptions[user_id]['tags'].remove(tag)
                removed_tags.append(tag)
        
        save_subscriptions(subscriptions)
        if removed_tags:
            await message.author.send(f"✅ You removed {', '.join(removed_tags)}")
            await message.channel.send(f"{message.author.mention} removed {', '.join(removed_tags)}")
        else:
            await message.author.send("❌ No matching tags found.")
            
    elif content == '!yc-news tags':
        subscriptions = load_subscriptions()
        user_id = str(message.author.id)
        
        if user_id not in subscriptions:
            await message.author.send("❌ You are not subscribed yet.")
            return
        
        tags = subscriptions[user_id].get('tags', [])
        if tags:
            await message.author.send(f"📋 Your current tags are {', '.join(tags)}")
        else:
            await message.author.send("📋 You have no tags subscribed. Use `!yc-news add=\"AI, ML\"` to add tags.")

try:
    client.run(DISCORD_TOKEN)
except discord.errors.LoginFailure:
    print("Error: Invalid Discord token. Please check your DISCORD_TOKEN in .env file.")
except KeyboardInterrupt:
    print("\nBot stopped by user.")
except Exception as e:
    print(f"Error starting bot: {e}")
