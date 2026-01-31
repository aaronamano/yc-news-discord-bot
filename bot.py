import os
import asyncio
import requests
import json
import discord
from discord.ext import tasks
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from supabase import create_client, Client
import time
import random
from typing import Optional

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
HN_URL = "https://news.ycombinator.com/newest"

# Rate limiting configuration
MAX_RETRIES = 5
BASE_RETRY_DELAY = 2  # seconds
MAX_RETRY_DELAY = 300  # 5 minutes
API_RATE_LIMIT = 45  # requests per minute (Discord limit is 50)
DM_RATE_LIMIT = 2  # DMs per second per user

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Validate required environment variables
if not DISCORD_TOKEN or not CHANNEL_ID or not SUPABASE_URL or not SUPABASE_KEY:
    exit(1)

# Initialize Discord client
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.dm_messages = True
client = discord.Client(intents=intents)

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def exponential_backoff(attempt: int) -> int:
    """Calculate exponential backoff delay with jitter"""
    delay = min(BASE_RETRY_DELAY * (2 ** attempt), MAX_RETRY_DELAY)
    # Add jitter to avoid thundering herd
    jitter = random.uniform(0.1, 0.5) * delay
    return int(delay + jitter)

async def rate_limit_check(operation_type: str = "api") -> bool:
    """Check if we're within rate limits"""
    global last_api_request, dm_cooldowns
    
    current_time = time.time()
    
    if operation_type == "api":
        # API rate limiting (45 requests per minute)
        last_minute_requests = [t for t in last_api_request.get("api", []) if current_time - t < 60]
        if len(last_minute_requests) >= API_RATE_LIMIT:
            return False
        
        if "api" not in last_api_request:
            last_api_request["api"] = []
        last_api_request["api"].append(current_time)
        
        # Keep only recent timestamps
        last_api_request["api"] = [t for t in last_api_request["api"] if current_time - t < 60]
        
    elif operation_type == "dm":
        # DM rate limiting per user
        if len(dm_cooldowns) >= DM_RATE_LIMIT:
            return False
    
    return True

async def wait_for_rate_limit(operation_type: str = "api"):
    """Wait until we're within rate limits"""
    while not await rate_limit_check(operation_type):
        if operation_type == "api":
            await asyncio.sleep(1.5)  # Wait for API rate limit reset
        else:
            await asyncio.sleep(0.6)  # Wait for DM rate limit reset

# Track posted story IDs to avoid duplicates
posted_ids = set()

# Rate limiting trackers
last_api_request = {}
dm_cooldowns = {}
connection_attempts = 0
last_connection_attempt = 0

def fetch_hn_stories():
    """Fetch latest stories from Hacker News"""
    try:
        response = requests.get(HN_URL, timeout=10)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        
        stories = []
        story_rows = soup.select("tr.athing")[:20]  # Get latest 20 stories
        
        for row in story_rows:
            story_id = row.get("id")
            title_link = row.select_one("span.titleline a")
            
            if not title_link:
                continue
                
            title = title_link.text.strip()
            url = title_link.get("href", "")
            hn_link = f"https://news.ycombinator.com/item?id={story_id}"
            
            # Get story age
            subtext = row.find_next_sibling("tr").select_one("td.subtext")
            age = subtext.select_one("span.age").text if subtext else "unknown"
            
            stories.append({
                "id": story_id,
                "title": title,
                "url": url,
                "hn_link": hn_link,
                "age": age
            })
            
        return stories
    except Exception:
        return []

def story_matches_keywords(title, url, keywords):
    """Check if story title or URL contains any keywords"""
    if not keywords:
        return True
    
    title_lower = title.lower()
    url_lower = url.lower()
    
    for keyword in keywords:
        keyword_lower = keyword.lower().strip()
        if keyword_lower and (keyword_lower in title_lower or keyword_lower in url_lower):
            return True
    
    return False

async def load_subscriptions():
    """Load user subscriptions from Supabase"""
    try:
        response = supabase.table('subscriptions').select('*').execute()
        if not response.data:
            return {}
        
        subscriptions = {}
        for row in response.data:
            subscriptions[row['userId']] = {
                'subscribed': bool(row['subscribed']),
                'tags': json.loads(row['tags']) if row['tags'] else []
            }
        return subscriptions
    except Exception:
        return {}

async def send_dm_to_user(user, story):
    """Send a story as DM to a user with rate limiting"""
    user_id = str(user.id)
    
    # Check DM cooldown
    current_time = time.time()
    if user_id in dm_cooldowns and current_time - dm_cooldowns[user_id] < 1.0:
        return False
    
    try:
        await wait_for_rate_limit("dm")
        
        source_link = story["hn_link"] if story["url"].startswith("item?id=") else story["url"]
        
        embed = discord.Embed(
            title=story["title"][:256],
            description=f"📰 **Source**: {source_link} | ⏰ **Age**: {story['age']}",
            url=source_link
        )
        
        await user.send(embed=embed)
        
        # Update DM cooldown
        dm_cooldowns[user_id] = current_time
        
        return True
    except discord.Forbidden:
        return False
    except discord.HTTPException as e:
        if e.status == 429:
            # Handle rate limit specifically
            retry_after = e.response.headers.get('Retry-After')
            if retry_after:
                await asyncio.sleep(int(retry_after) + 1)
        return False
    except Exception:
        return False

@tasks.loop(hours=1)
async def send_news_dms():
    """Send news to subscribed users based on keywords with rate limiting"""
    try:
        await wait_for_rate_limit("api")
        subscriptions = await load_subscriptions()
        if not subscriptions:
            return
        
        stories = fetch_hn_stories()
        new_stories = [s for s in stories if s["id"] not in posted_ids]
        
        if not new_stories:
            return
    except Exception:
        return
    
    # Process each subscribed user
    for user_id, user_data in subscriptions.items():
        if not user_data.get('subscribed'):
            continue
        
        keywords = user_data.get('tags', [])
        if not keywords:
            continue
        
        # Find stories matching user's keywords
        matching_stories = []
        for story in new_stories:
            if story_matches_keywords(story["title"], story["url"], keywords):
                matching_stories.append(story)
        
        # Send top matching stories (limit to 2 per hour)
        if matching_stories:
            try:
                await wait_for_rate_limit("api")
                user = await client.fetch_user(int(user_id))
                if user:
                    for story in matching_stories[:2]:
                        if await send_dm_to_user(user, story):
                            await asyncio.sleep(1.5)  # Longer delay between DMs to avoid rate limits
                        else:
                            break  # Stop if DMs are forbidden
            except discord.HTTPException as e:
                if e.status == 429:
                    # Handle rate limit at fetch user level
                    retry_after = e.response.headers.get('Retry-After')
                    if retry_after:
                        await asyncio.sleep(int(retry_after) + 1)
                continue
            except Exception:
                continue
    
    # Mark stories as posted
    for story in new_stories:
        posted_ids.add(story["id"])

@client.event
async def on_message(message):
    """Handle bot commands"""
    if message.author == client.user:
        return
    
    if message.channel.id != CHANNEL_ID:
        return
    
    content = message.content.strip()
    
    if content.startswith('!yc-news subscribe'):
        try:
            await wait_for_rate_limit("api")
            subscriptions = await load_subscriptions()
            user_id = str(message.author.id)
            
            if user_id not in subscriptions:
                subscriptions[user_id] = {'subscribed': True, 'tags': []}
            else:
                subscriptions[user_id]['subscribed'] = True
            
            supabase.table('subscriptions').upsert({
                'userId': user_id,
                'subscribed': True,
                'tags': json.dumps(subscriptions[user_id]['tags'])
            }).execute()
            
            await message.author.send("✅ You have been subscribed to YC News updates!")
            await message.channel.send(f"{message.author.mention} subscribed")
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(2)
            await message.channel.send("❌ Error processing subscription. Please try again later.")
        except Exception:
            await message.channel.send("❌ Error processing subscription. Please try again.")
    
    elif content.startswith('!yc-news unsubscribe'):
        try:
            subscriptions = await load_subscriptions()
            user_id = str(message.author.id)
            
            if user_id in subscriptions:
                subscriptions[user_id]['subscribed'] = False
            
            supabase.table('subscriptions').upsert({
                'userId': user_id,
                'subscribed': False,
                'tags': json.dumps(subscriptions[user_id]['tags'] if user_id in subscriptions else [])
            }).execute()
            
            await message.author.send("❌ You have been unsubscribed from YC News updates.")
            await message.channel.send(f"{message.author.mention} unsubscribed")
        except Exception:
            await message.channel.send("❌ Error processing unsubscription. Please try again.")
    
    elif content.startswith('!yc-news add='):
        try:
            subscriptions = await load_subscriptions()
            user_id = str(message.author.id)
            
            if user_id not in subscriptions:
                subscriptions[user_id] = {'subscribed': True, 'tags': []}
            
            tags_str = content.split('=', 1)[1].strip()
            if tags_str.startswith('"') and tags_str.endswith('"'):
                tags_str = tags_str[1:-1]
            
            new_tags = [tag.strip() for tag in tags_str.split(',') if tag.strip()]
            
            if 'tags' not in subscriptions[user_id]:
                subscriptions[user_id]['tags'] = []
                
            for tag in new_tags:
                if tag not in subscriptions[user_id]['tags']:
                    subscriptions[user_id]['tags'].append(tag)
            
            supabase.table('subscriptions').upsert({
                'userId': user_id,
                'subscribed': subscriptions[user_id]['subscribed'],
                'tags': json.dumps(subscriptions[user_id]['tags'])
            }).execute()
            
            await message.author.send(f"✅ You added {', '.join(new_tags)}")
            await message.channel.send(f"{message.author.mention} added \"{', '.join(new_tags)}\"")
        except Exception:
            await message.channel.send("❌ Error adding tags. Please try again.")
    
    elif content.startswith('!yc-news remove='):
        try:
            subscriptions = await load_subscriptions()
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
            
            supabase.table('subscriptions').upsert({
                'userId': user_id,
                'subscribed': subscriptions[user_id]['subscribed'],
                'tags': json.dumps(subscriptions[user_id]['tags'])
            }).execute()
            
            if removed_tags:
                await message.author.send(f"✅ You removed {', '.join(removed_tags)}")
                await message.channel.send(f"{message.author.mention} removed {', '.join(removed_tags)}")
            else:
                await message.author.send("❌ No matching tags found.")
        except Exception:
            await message.channel.send("❌ Error removing tags. Please try again.")
    
    elif content == '!yc-news tags':
        try:
            subscriptions = await load_subscriptions()
            user_id = str(message.author.id)
            
            if user_id not in subscriptions:
                await message.author.send("❌ You are not subscribed yet.")
                return
            
            tags = subscriptions[user_id].get('tags', [])
            if tags:
                await message.author.send(f"📋 Your current tags are {', '.join(tags)}")
            else:
                await message.author.send("📋 You have no tags subscribed. Use `!yc-news add=\"AI, ML\"` to add tags.")
        except Exception:
            await message.channel.send("❌ Error retrieving tags. Please try again.")

async def run_bot_with_retry():
    """Run the bot with exponential backoff retry logic"""
    global connection_attempts, last_connection_attempt
    
    for attempt in range(MAX_RETRIES):
        try:
            connection_attempts = attempt + 1
            last_connection_attempt = time.time()
            
            # Add delay between connection attempts
            if attempt > 0:
                delay = exponential_backoff(attempt)
                print(f"[INFO] Waiting {delay}s before retry {attempt + 1}/{MAX_RETRIES}")
                await asyncio.sleep(delay)
            
            # Configure client with proper User-Agent
            await client.login(DISCORD_TOKEN)
            await client.connect()
            
            print(f"[INFO] Bot connected successfully on attempt {attempt + 1}")
            break
            
        except discord.HTTPException as e:
            if e.status == 429:
                retry_after = e.response.headers.get('Retry-After') if e.response else None
                if retry_after:
                    wait_time = int(retry_after) + random.randint(1, 5)
                    print(f"[INFO] Rate limited. Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
                else:
                    wait_time = exponential_backoff(attempt)
                    print(f"[INFO] Rate limited. Waiting {wait_time}s before retry...")
                    await asyncio.sleep(wait_time)
            elif "HTML" in str(e) or "doctype" in str(e).lower():
                # Likely Cloudflare protection
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
    send_news_dms.start()

@client.event
async def on_disconnect():
    print("[INFO] Bot disconnected")

if __name__ == "__main__":
    # Set User-Agent for requests
    if hasattr(requests, 'Session'):
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (compatible; YCNewsBot/1.0; +https://github.com/yc-news-bot)'
        })
    
    # Run the bot with retry logic
    asyncio.run(run_bot_with_retry())