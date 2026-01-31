import os
import asyncio
import requests
import json
import signal
import sys
import discord
import time
import re
from discord.ext import tasks
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from supabase import create_client, Client
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))  # Default to 0 if not set

# Validate required environment variables
if not DISCORD_TOKEN:
    exit(1)

if not CHANNEL_ID:
    exit(1)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

# Removed complex connection tracking - using simple client.run() approach

posted_ids = set()

# Rate limiting configuration
DISCORD_RATE_LIMIT = {
    'fetch_user_delay': 0.1,  # 100ms between user fetches (10 per second)
    'dm_delay': 1.0,  # 1 second between DMs (1 per second)
    'batch_size': 5,  # Process users in batches
    'batch_delay': 5.0  # 5 seconds between batches
}

# Web scraping configuration
HN_RATE_LIMIT = {
    'scrape_delay': 10.0,  # 10 seconds between scrapes
    'max_stories': 30,  # Maximum stories to process
    'user_agent': 'Mozilla/5.0 (compatible; YCNewsBot/1.0; +https://github.com/example/bot)'
}
last_user_fetch_time = 0
last_dm_time = 0

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    exit(1)

# Initialize Supabase client with error handling
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    exit(1)

async def init_db():
    """Initialize Supabase - ensure table exists"""
    try:
        # Supabase handles table creation automatically
        pass
    except Exception as e:
        raise

async def rate_limit_wait(category='discord', delay=None):
    """Implement rate limiting delays"""
    if category == 'hn_scrape':
        # Rate limiting for HN scraping
        await asyncio.sleep(HN_RATE_LIMIT['scrape_delay'])
    
    elif category == 'discord_fetch':
        global last_user_fetch_time
        current_time = time.time()
        time_since_last = current_time - last_user_fetch_time
        if time_since_last < DISCORD_RATE_LIMIT['fetch_user_delay']:
            await asyncio.sleep(DISCORD_RATE_LIMIT['fetch_user_delay'] - time_since_last)
        last_user_fetch_time = time.time()
    
    elif category == 'discord_dm':
        global last_dm_time
        current_time = time.time()
        time_since_last = current_time - last_dm_time
        if time_since_last < DISCORD_RATE_LIMIT['dm_delay']:
            await asyncio.sleep(DISCORD_RATE_LIMIT['dm_delay'] - time_since_last)
        last_dm_time = time.time()

async def retry_with_backoff(func, max_retries=3, base_delay=1):
    """Retry function with exponential backoff"""
    for attempt in range(max_retries):
        try:
            return await func()
        except discord.HTTPException as e:
            if hasattr(e, 'status') and e.status == 429:
                wait_time = base_delay * (2 ** attempt) + (attempt * 0.1)  # Add jitter
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise
            else:
                raise
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait_time = base_delay * (2 ** attempt)
            await asyncio.sleep(wait_time)

async def load_subscriptions():
    """Load all subscriptions from Supabase"""
    try:
        response = supabase.table('subscriptions').select('*').execute()
        
        if response.data is None:
            return {}
        
        subscriptions = {}
        for row in response.data:
            subscriptions[row['userId']] = {
                'subscribed': bool(row['subscribed']),
                'tags': json.loads(row['tags']) if row['tags'] else []
            }
        return subscriptions
    except Exception as e:
        return {}  # Return empty dict to prevent crashes

async def save_subscriptions(subscriptions):
    """Save all subscriptions to Supabase"""
    try:
        # Upsert all subscription records
        for user_id, user_data in subscriptions.items():
            supabase.table('subscriptions').upsert({
                'userId': user_id,
                'subscribed': user_data['subscribed'],
                'tags': json.dumps(user_data['tags'])
            }).execute()
    except Exception as e:
        # Don't crash bot, just handle the error
        pass

# REMOVED: fetch_meta_data function to prevent 429 rate limiting errors

async def fetch_hn_newest(tags=None):
    """Fetch newest stories from Hacker News and filter by keywords"""
    await rate_limit_wait('hn_scrape')
    
    try:
        headers = {
            'User-Agent': HN_RATE_LIMIT['user_agent']
        }
        
        response = requests.get('https://news.ycombinator.com/newest', headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        items = []
        
        # Find all story rows
        story_rows = soup.find_all('tr', class_='athing')
        
        for row in story_rows[:HN_RATE_LIMIT['max_stories']]:
            try:
                # Extract story ID from the row
                story_id = row.get('id', '')
                if not story_id:
                    continue
                
                # Find title and link
                title_span = row.find('span', class_='titleline')
                if not title_span:
                    continue
                    
                title_link = title_span.find('a')
                if not title_link:
                    continue
                
                title = title_link.get_text(strip=True)
                story_url = title_link.get('href', '')
                
                # Make relative URLs absolute
                if story_url.startswith('item?id='):
                    hn_link = f"https://news.ycombinator.com/{story_url}"
                    story_url = hn_link
                else:
                    hn_link = f"https://news.ycombinator.com/item?id={story_id}"
                
                # Extract age from the next row
                next_row = row.find_next_sibling('tr')
                age = "Unknown"
                if next_row:
                    age_span = next_row.find('span', class_='age')
                    if age_span:
                        age = age_span.get_text(strip=True)
                
                # Check if story matches tags/keywords
                if tags and not story_matches_tags(title, story_url, tags):
                    continue
                
                items.append({
                    "id": story_id,
                    "title": title,
                    "url": story_url,
                    "hn_link": hn_link,
                    "age": age,
                })
                
                # Limit results to prevent overwhelming
                if len(items) >= 15:
                    break
                    
            except Exception as e:
                continue
        
        return items
        
    except Exception as e:
        return []

def story_matches_tags(title, url, tags):
    """Check if story title or URL contains any of the specified tags"""
    if not tags:
        return True
    
    title_lower = title.lower()
    url_lower = url.lower()
    
    for tag in tags:
        tag_lower = tag.lower().strip()
        if tag_lower and (tag_lower in title_lower or tag_lower in url_lower):
            return True
    
    return False

def fetch_newest(top_n=15, tags=None):
    """Synchronous version for backward compatibility"""
    try:
        headers = {
            'User-Agent': HN_RATE_LIMIT['user_agent']
        }
        
        response = requests.get('https://news.ycombinator.com/newest', headers=headers, timeout=15)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        items = []
        
        # Find all story rows
        story_rows = soup.find_all('tr', class_='athing')
        
        for row in story_rows[:HN_RATE_LIMIT['max_stories']]:
            try:
                # Extract story ID from the row
                story_id = row.get('id', '')
                if not story_id:
                    continue
                
                # Find title and link
                title_span = row.find('span', class_='titleline')
                if not title_span:
                    continue
                    
                title_link = title_span.find('a')
                if not title_link:
                    continue
                
                title = title_link.get_text(strip=True)
                story_url = title_link.get('href', '')
                
                # Make relative URLs absolute
                if story_url.startswith('item?id='):
                    hn_link = f"https://news.ycombinator.com/{story_url}"
                    story_url = hn_link
                else:
                    hn_link = f"https://news.ycombinator.com/item?id={story_id}"
                
                # Extract age from the next row
                next_row = row.find_next_sibling('tr')
                age = "Unknown"
                if next_row:
                    age_span = next_row.find('span', class_='age')
                    if age_span:
                        age = age_span.get_text(strip=True)
                
                # Check if story matches tags/keywords
                if tags and not story_matches_tags(title, story_url, tags):
                    continue
                
                items.append({
                    "id": story_id,
                    "title": title,
                    "url": story_url,
                    "hn_link": hn_link,
                    "age": age,
                })
                
                # Limit results to prevent overwhelming
                if len(items) >= top_n:
                    break
                    
            except Exception as e:
                continue
        
        return items
        
    except Exception as e:
        return []

# REMOVED: safe_send_dm function - sending DMs directly to reduce complexity

@tasks.loop(hours=1)
async def poll_hn():
        subscriptions = await load_subscriptions()
        subscribed_count = len([s for s in subscriptions.values() if s.get('subscribed')])
        
        # Collect all unique tag combinations to minimize API calls
        tag_combinations = {}
        for user_id, user_data in subscriptions.items():
            if not user_data.get('subscribed', False):
                continue
            
            tags = tuple(sorted(user_data.get('tags', [])))  # Use tuple for dict key
            if tags not in tag_combinations:
                tag_combinations[tags] = []
            tag_combinations[tags].append(user_id)
        
        # Fetch items once per unique tag combination with rate limiting
        all_new_items = {}
        for tags, user_list in tag_combinations.items():
            items = await fetch_hn_newest(list(tags))  # Use new HN scraping method
            new_items = [it for it in items if it["id"] not in posted_ids]
            if new_items:
                all_new_items[tags] = new_items[:2]  # Limit to 2 items per tag combination
                for item in new_items[:2]:
                    posted_ids.add(item["id"])
        
        if not all_new_items:
            return
        
        # Batch process users to avoid rate limits
        all_users_to_process = []
        for tags, user_list in tag_combinations.items():
            if tags not in all_new_items:
                continue
            for user_id in user_list:
                all_users_to_process.append((user_id, all_new_items[tags]))
        

        
        # Process users in batches with rate limiting
        for i in range(0, len(all_users_to_process), DISCORD_RATE_LIMIT['batch_size']):
            batch = all_users_to_process[i:i + DISCORD_RATE_LIMIT['batch_size']]
            
            # Process each user in the batch
            for user_id, items in batch:
                try:
                    # Rate limit user fetching
                    await rate_limit_wait('discord_fetch')
                    
                    # Fetch user with retry logic
                    async def fetch_user_with_cloudflare_retry():
                        try:
                            return await client.fetch_user(int(user_id))
                        except discord.HTTPException as e:
                            if '429' in str(e) and is_cloudflare_block_error(str(e)):
                                raise  # Will be handled by retry_with_backoff
                            else:
                                raise
                    
                    user = await retry_with_backoff(fetch_user_with_cloudflare_retry, max_retries=3)
                    
                    if not user:
                        continue
                    
                    # Send items to this user with rate limiting
                    for item in items:
                        source_link = item["hn_link"] if item["url"].startswith("item?id=") else item["url"]
                        
                        embed = discord.Embed(
                            title=item["title"][:256],
                            description=f"📰 **Source**: {source_link} | ⏰ **Age**: {item['age']}",
                            url=source_link
                        )

                        # Rate limit DM sending with retry logic
                        await rate_limit_wait('discord_dm')
                        
                        async def send_dm_with_cloudflare_retry():
                            try:
                                return await user.send(embed=embed)
                            except discord.HTTPException as e:
                                if '429' in str(e) and is_cloudflare_block_error(str(e)):
                                    raise  # Will be handled by retry_with_backoff
                                else:
                                    raise
                        
                        await retry_with_backoff(send_dm_with_cloudflare_retry, max_retries=3)
                        
                except discord.Forbidden:
                    continue
                except Exception as e:
                    continue
            
            # Add delay between batches
            if i + DISCORD_RATE_LIMIT['batch_size'] < len(all_users_to_process):
                await asyncio.sleep(DISCORD_RATE_LIMIT['batch_delay'])

@client.event
async def on_ready():
    await init_db()
    
    # Start health monitoring
    asyncio.create_task(check_connection_health())
    
    # Start polling
    poll_hn.start()

@client.event
async def on_disconnect():
    """Handle disconnection and clean up sessions"""
    if poll_hn.is_running():
        poll_hn.stop()
    await asyncio.sleep(1)  # Give tasks time to clean up
    
    # Check if this was due to rate limiting
    await asyncio.sleep(5)  # Brief pause before potential reconnection

@client.event
async def on_resumed():
    """Handle successful reconnection"""
    if not poll_hn.is_running():
        poll_hn.start()

async def check_connection_health():
    """Monitor connection health and handle issues"""
    while client.is_ready():
        try:
            # Test connection with a simple API call
            await client.fetch_user(client.user.id)
            await asyncio.sleep(300)  # Check every 5 minutes
        except discord.HTTPException as e:
            if '429' in str(e):
                await asyncio.sleep(60)  # Wait longer if rate limited
            else:
                await asyncio.sleep(30)
        except Exception:
            await asyncio.sleep(60)

def signal_handler(sig, frame):
    """Handle graceful shutdown"""
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(client.close())
    else:
        asyncio.run(client.close())
    sys.exit(0)

@client.event
async def on_message(message):
    try:
        if message.author == client.user:
            return
        
        # Only allow commands in the specified channel
        if message.channel.id != CHANNEL_ID:
            return
        
        content = message.content.strip()
        
        if content.startswith('!yc-news subscribe'):
            try:
                subscriptions = await load_subscriptions()
                user_id = str(message.author.id)
                
                if user_id not in subscriptions:
                    subscriptions[user_id] = {'subscribed': True, 'tags': []}
                else:
                    subscriptions[user_id]['subscribed'] = True
                
                await save_subscriptions(subscriptions)
                await message.author.send("✅ You have been subscribed to YC News updates!")
                await message.channel.send(f"{message.author.mention} subscribed")
            except Exception as e:
                await message.channel.send("❌ Error processing subscription. Please try again.")
                
        elif content.startswith('!yc-news unsubscribe'):
            try:
                subscriptions = await load_subscriptions()
                user_id = str(message.author.id)
                
                if user_id in subscriptions:
                    subscriptions[user_id]['subscribed'] = False
                
                await save_subscriptions(subscriptions)
                await message.author.send("❌ You have been unsubscribed from YC News updates.")
                await message.channel.send(f"{message.author.mention} unsubscribed")
            except Exception as e:
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
                
                if user_id not in subscriptions:
                    subscriptions[user_id] = {'subscribed': True, 'tags': []}
                if 'tags' not in subscriptions[user_id]:
                    subscriptions[user_id]['tags'] = []
                    
                for tag in new_tags:
                    if tag not in subscriptions[user_id]['tags']:
                        subscriptions[user_id]['tags'].append(tag)
                
                await save_subscriptions(subscriptions)
                await message.author.send(f"✅ You added {', '.join(new_tags)}")
                await message.channel.send(f"{message.author.mention} added \"{', '.join(new_tags)}\"")
            except Exception as e:
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
                
                await save_subscriptions(subscriptions)
                if removed_tags:
                    await message.author.send(f"✅ You removed {', '.join(removed_tags)}")
                    await message.channel.send(f"{message.author.mention} removed {', '.join(removed_tags)}")
                else:
                    await message.author.send("❌ No matching tags found.")
            except Exception as e:
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
            except Exception as e:
                await message.channel.send("❌ Error retrieving tags. Please try again.")
            
    except Exception as e:
        # Send error message to user
        try:
            await message.channel.send("❌ An error occurred while processing your command. Please try again.")
        except:
            pass  # Don't crash if we can't send error message

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

def is_cloudflare_block_error(error_text):
    """Detect if error is Cloudflare block page"""
    return (
        isinstance(error_text, str) and
        ('<!doctype html>' in error_text.lower() or 
         'cloudflare' in error_text.lower() or
         'error 1015' in error_text.lower() or
         'ray id:' in error_text.lower())
    )

async def start_bot_with_retry():
    """Start bot with intelligent retry logic for Cloudflare blocks"""
    max_retries = 10
    base_delay = 60  # Start with 1 minute
    max_delay = 3600  # Max 1 hour wait
    
    for attempt in range(max_retries):
        try:
            await client.start(DISCORD_TOKEN, reconnect=True)
            return  # Success, exit function
            
        except discord.errors.HTTPException as e:
            error_text = str(e)
            
            if '429' in error_text and is_cloudflare_block_error(error_text):
                # Cloudflare IP ban detected
                delay = min(base_delay * (2 ** attempt), max_delay)
                jitter = delay * 0.1 * (0.5 + (hash(str(attempt)) % 100) / 100)
                total_delay = delay + jitter
                
                # Reset client connection to avoid hanging
                try:
                    await client.close()
                except:
                    pass
                
                await asyncio.sleep(total_delay)
                continue
                
            elif '429' in error_text:
                # Regular rate limit, shorter delay
                delay = 30 + (attempt * 10)
                await asyncio.sleep(delay)
                continue
                
            else:
                # Different error, re-raise
                raise
                
        except discord.errors.LoginFailure:
            return
            
        except KeyboardInterrupt:
            return
            
        except Exception as e:
            error_text = str(e)
            if is_cloudflare_block_error(error_text):
                # Handle Cloudflare errors that might not come through HTTPException
                delay = min(base_delay * (2 ** attempt), max_delay)
                await asyncio.sleep(delay)
                continue
            else:
                if attempt >= max_retries - 1:
                    import traceback
                    traceback.print_exc()
                else:
                    await asyncio.sleep(60)  # Wait 1 minute before retry

# Main execution - enhanced with retry logic
if __name__ == "__main__":
    asyncio.run(start_bot_with_retry())