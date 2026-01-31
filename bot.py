import os
import asyncio
import requests
import json
import time
import sqlite3
from bs4 import BeautifulSoup
import discord
from discord.ext import tasks
from dotenv import load_dotenv
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

posted_ids = set()
DB_NAME = os.path.join(os.getenv('DATA_DIR', '/data'), 'subscriptions.db')

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS subscriptions (
            userId TEXT PRIMARY KEY,
            subscribed BOOLEAN DEFAULT FALSE,
            tags TEXT DEFAULT '[]'
        )
    ''')
    conn.commit()
    conn.close()

def get_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA synchronous=NORMAL')
    conn.execute('PRAGMA cache_size=10000')
    return conn

def load_subscriptions():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM subscriptions')
    rows = cursor.fetchall()
    conn.close()
    
    subscriptions = {}
    for row in rows:
        subscriptions[row['userId']] = {
            'subscribed': bool(row['subscribed']),
            'tags': json.loads(row['tags']) if row['tags'] else []
        }
    return subscriptions

def save_subscriptions(subscriptions):
    conn = get_connection()
    cursor = conn.cursor()
    
    for user_id, user_data in subscriptions.items():
        cursor.execute('''
            INSERT OR REPLACE INTO subscriptions (userId, subscribed, tags)
            VALUES (?, ?, ?)
        ''', (user_id, user_data['subscribed'], json.dumps(user_data['tags'])))
    
    conn.commit()
    conn.close()

import time
import random

def fetch_meta_data(url):
    """Fetch meta description and image from a URL with retry logic"""
    # Skip internal HN links
    if url.startswith("item?id="):
        return None, None
    
    # Ensure URL is absolute
    if not url.startswith(("http://", "https://")):
        url = "https://news.ycombinator.com/" + url
    
    # Multiple user agents to rotate through
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ]
    
    # Enhanced headers to look more like a real browser
    base_headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0"
    }
    
    max_retries = 3
    base_timeout = 15  # Increased from 10 to 15 seconds
    
    for attempt in range(max_retries):
        try:
            # Rotate user agent
            headers = base_headers.copy()
            headers["User-Agent"] = random.choice(user_agents)
            
            # Exponential backoff for timeout
            timeout = base_timeout * (2 ** attempt)
            
            response = requests.get(url, timeout=timeout, headers=headers)
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
            
        except requests.exceptions.Timeout as e:
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                print(f"Timeout for {url}, retry {attempt + 1}/{max_retries} after {wait_time:.1f}s")
                time.sleep(wait_time)
            else:
                print(f"Timeout error fetching meta data for {url}: {e}")
                return None, None
                
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code
            if status_code == 403:
                # Try one more time with a different user agent for 403 errors
                if attempt < max_retries - 1:
                    print(f"403 Forbidden for {url}, retry {attempt + 1}/{max_retries} with different user agent")
                    time.sleep(2)
                    continue
                else:
                    print(f"403 Forbidden error fetching meta data for {url}")
                    return None, None
            elif status_code in [404, 410]:
                # Don't retry for client errors (except 403)
                print(f"Client error {status_code} for {url}: {e}")
                return None, None
            else:
                # Retry for other HTTP errors
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + random.uniform(0, 1)
                    print(f"HTTP error {status_code} for {url}, retry {attempt + 1}/{max_retries} after {wait_time:.1f}s")
                    time.sleep(wait_time)
                else:
                    print(f"HTTP error fetching meta data for {url}: {e}")
                    return None, None
                    
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait_time = (2 ** attempt) + random.uniform(0, 1)
                print(f"Request error for {url}, retry {attempt + 1}/{max_retries} after {wait_time:.1f}s: {e}")
                time.sleep(wait_time)
            else:
                print(f"Request error fetching meta data for {url}: {e}")
                return None, None
                
        except Exception as e:
            print(f"Unexpected error fetching meta data for {url}: {e}")
            return None, None
    
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

@tasks.loop(hours=1)
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
                
                # Always include the URL in the description
                if description:
                    base_desc = description
                    extra = f"\n\n📰 **Source**: {source_link}"
                else:
                    # If no description was fetched, still include the URL
                    base_desc = "No description available"
                    extra = f"\n\n📰 **Source**: {source_link}"

                full_desc = (base_desc + extra)
                if len(full_desc) > 4000:
                    full_desc = full_desc[:4000]

                embed = discord.Embed(
                    title=item["title"][:256],
                    description=full_desc,
                    url=source_link  # Make the title clickable
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
    init_db()
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
