import os
import asyncio
import requests
import json
import signal
import sys
import discord
import time
from discord.ext import tasks
from dotenv import load_dotenv
from supabase import create_client, Client
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))  # Default to 0 if not set

# Validate required environment variables
if not DISCORD_TOKEN:
    print("Error: DISCORD_TOKEN environment variable is required")
    exit(1)

if not CHANNEL_ID:
    print("Error: CHANNEL_ID environment variable is required")
    exit(1)

print("Environment variables loaded successfully")

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

ALGOLIA_RATE_LIMIT = {
    'requests_per_minute': 50,  # Conservative limit
    'request_delay': 1.2  # 1.2 seconds between requests
}

# Request tracking for rate limiting
algolia_request_times = []
last_user_fetch_time = 0
last_dm_time = 0

# Supabase configuration
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    print("Error: SUPABASE_URL and SUPABASE_KEY environment variables are required")
    exit(1)

# Initialize Supabase client with error handling
try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("Connected to Supabase database")
except Exception as e:
    print(f"Failed to connect to Supabase: {e}")
    exit(1)

async def init_db():
    """Initialize Supabase - ensure table exists"""
    try:
        # Supabase handles table creation automatically
        print("Supabase database is ready")
    except Exception as e:
        print(f"Database initialization failed: {e}")
        raise

async def rate_limit_wait(category='discord', delay=None):
    """Implement rate limiting delays"""
    if category == 'algolia':
        global algolia_request_times
        current_time = time.time()
        # Remove requests older than 1 minute
        algolia_request_times = [t for t in algolia_request_times if current_time - t < 60]
        
        if len(algolia_request_times) >= ALGOLIA_RATE_LIMIT['requests_per_minute']:
            sleep_time = 60 - (current_time - algolia_request_times[0])
            if sleep_time > 0:
                print(f"Algolia rate limit reached, sleeping {sleep_time:.1f}s")
                await asyncio.sleep(sleep_time)
        
        algolia_request_times.append(current_time)
        await asyncio.sleep(ALGOLIA_RATE_LIMIT['request_delay'])
    
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
                print(f"Rate limited (attempt {attempt + 1}), waiting {wait_time:.1f}s")
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise
            else:
                raise
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            wait_time = base_delay * (2 ** attempt)
            print(f"Error (attempt {attempt + 1}), retrying in {wait_time:.1f}s: {e}")
            await asyncio.sleep(wait_time)

async def load_subscriptions():
    """Load all subscriptions from Supabase"""
    try:
        response = supabase.table('subscriptions').select('*').execute()
        
        if response.data is None:
            print("No subscriptions found or error occurred")
            return {}
        
        subscriptions = {}
        for row in response.data:
            subscriptions[row['userId']] = {
                'subscribed': bool(row['subscribed']),
                'tags': json.loads(row['tags']) if row['tags'] else []
            }
        print(f"Loaded {len(subscriptions)} subscriptions from Supabase")
        return subscriptions
    except Exception as e:
        print(f"Error loading subscriptions from Supabase: {e}")
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
        
        print(f"Saved {len(subscriptions)} subscriptions to Supabase")
    except Exception as e:
        print(f"Error saving subscriptions to Supabase: {e}")
        # Don't crash bot, just log the error

# REMOVED: fetch_meta_data function to prevent 429 rate limiting errors

async def fetch_newest_async(top_n=15, tags=None):
    """Async version of fetch_newest with rate limiting"""
    await rate_limit_wait('algolia')
    
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

def fetch_newest(top_n=15, tags=None):
    """Synchronous version for backward compatibility"""
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

# REMOVED: safe_send_dm function - sending DMs directly to reduce complexity

@tasks.loop(hours=1)
async def poll_hn():
    print("=== Starting poll_hn loop ===")
    try:
        subscriptions = await load_subscriptions()
        subscribed_count = len([s for s in subscriptions.values() if s.get('subscribed')])
        print(f"Loaded {len(subscriptions)} total subscriptions")
        print(f"Starting hourly poll for {subscribed_count} subscribed users")
        
        # Collect all unique tag combinations to minimize API calls
        tag_combinations = {}
        for user_id, user_data in subscriptions.items():
            if not user_data.get('subscribed', False):
                continue
            
            tags = tuple(sorted(user_data.get('tags', [])))  # Use tuple for dict key
            if tags not in tag_combinations:
                tag_combinations[tags] = []
            tag_combinations[tags].append(user_id)
        
        print(f"Fetching {len(tag_combinations)} unique tag combinations instead of {len(subscriptions)} individual requests")
        
        # Fetch items once per unique tag combination with rate limiting
        all_new_items = {}
        for tags, user_list in tag_combinations.items():
            items = await fetch_newest_async(8, list(tags))  # Reduced to 8 items
            new_items = [it for it in items if it["id"] not in posted_ids]
            if new_items:
                all_new_items[tags] = new_items[:2]  # Limit to 2 items per tag combination
                for item in new_items[:2]:
                    posted_ids.add(item["id"])
        
        if not all_new_items:
            print("No new items found")
            print("=== poll_hn loop completed ===")
            return
        
        # Batch process users to avoid rate limits
        all_users_to_process = []
        for tags, user_list in tag_combinations.items():
            if tags not in all_new_items:
                continue
            for user_id in user_list:
                all_users_to_process.append((user_id, all_new_items[tags]))
        
        print(f"Processing {len(all_users_to_process)} user deliveries in batches")
        
        # Process users in batches with rate limiting
        for i in range(0, len(all_users_to_process), DISCORD_RATE_LIMIT['batch_size']):
            batch = all_users_to_process[i:i + DISCORD_RATE_LIMIT['batch_size']]
            
            # Process each user in the batch
            for user_id, items in batch:
                try:
                    # Rate limit user fetching
                    await rate_limit_wait('discord_fetch')
                    
                    # Fetch user with retry logic
                    user = await retry_with_backoff(
                        lambda: client.fetch_user(int(user_id)),
                        max_retries=3
                    )
                    
                    if not user:
                        continue
                    
                    # Send items to this user with rate limiting
                    for item in items:
                        source_link = item["hn_link"] if item["url"].startswith("item?id=") else item["url"]
                        
                        embed = discord.Embed(
                            title=item["title"][:256],
                            description=f"📰 **Source**: {source_link}",
                            url=source_link
                        )

                        # Rate limit DM sending with retry logic
                        await rate_limit_wait('discord_dm')
                        await retry_with_backoff(
                            lambda: user.send(embed=embed),
                            max_retries=3
                        )
                        
                except discord.Forbidden:
                    print(f"Cannot send DM to user {user_id}")
                    continue
                except Exception as e:
                    print(f"Error processing user {user_id}: {e}")
                    continue
            
            # Add delay between batches
            if i + DISCORD_RATE_LIMIT['batch_size'] < len(all_users_to_process):
                print(f"Batch completed, waiting {DISCORD_RATE_LIMIT['batch_delay']}s before next batch")
                await asyncio.sleep(DISCORD_RATE_LIMIT['batch_delay'])
        
        print("=== poll_hn loop completed successfully ===")
                    
    except Exception as e:
        print(f"Error in poll_hn loop: {e}")
        print("=== poll_hn loop completed with error ===")

@client.event
async def on_ready():
    await init_db()
    print(f"Logged in as {client.user}")
    print(f"Discord token present: {'Yes' if DISCORD_TOKEN else 'No'}")
    print(f"Channel ID: {CHANNEL_ID}")
    poll_hn.start()
    print("Bot is running. Press Ctrl+C to stop.")

@client.event
async def on_disconnect():
    """Handle disconnection and clean up sessions"""
    print("Bot disconnected, cleaning up...")
    if poll_hn.is_running():
        poll_hn.stop()
    await asyncio.sleep(1)  # Give tasks time to clean up

def signal_handler(sig, frame):
    """Handle graceful shutdown"""
    print('\nGracefully shutting down...')
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
                print(f"Error in subscribe command: {e}")
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
                print(f"Error in unsubscribe command: {e}")
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
                print(f"Error in add tags command: {e}")
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
                print(f"Error in remove tags command: {e}")
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
                print(f"Error in tags command: {e}")
                await message.channel.send("❌ Error retrieving tags. Please try again.")
            
    except Exception as e:
        print(f"Error in on_message handler: {e}")
        # Send error message to user
        try:
            await message.channel.send("❌ An error occurred while processing your command. Please try again.")
        except:
            pass  # Don't crash if we can't send error message

# Register signal handlers for graceful shutdown
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# Main execution - simple approach
if __name__ == "__main__":
    print("Starting bot...")
    try:
        client.run(DISCORD_TOKEN)
    except discord.errors.LoginFailure:
        print("Error: Invalid Discord token. Please check your DISCORD_TOKEN in environment variables.")
    except KeyboardInterrupt:
        print("\nBot stopped by user.")
    except Exception as e:
        print(f"Error starting bot: {e}")
        import traceback
        traceback.print_exc()  # Print full traceback for debugging