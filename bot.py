import os
import asyncio
import requests
import json
import discord
from discord.ext import tasks
from dotenv import load_dotenv
from bs4 import BeautifulSoup
from supabase import create_client, Client

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
HN_URL = "https://news.ycombinator.com/newest"

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

# Track posted story IDs to avoid duplicates
posted_ids = set()

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
    """Send a story as DM to a user"""
    try:
        source_link = story["hn_link"] if story["url"].startswith("item?id=") else story["url"]
        
        embed = discord.Embed(
            title=story["title"][:256],
            description=f"📰 **Source**: {source_link} | ⏰ **Age**: {story['age']}",
            url=source_link
        )
        
        await user.send(embed=embed)
        return True
    except discord.Forbidden:
        return False
    except Exception:
        return False

@tasks.loop(hours=1)
async def send_news_dms():
    """Send news to subscribed users based on keywords"""
    subscriptions = await load_subscriptions()
    if not subscriptions:
        return
    
    stories = fetch_hn_stories()
    new_stories = [s for s in stories if s["id"] not in posted_ids]
    
    if not new_stories:
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
                user = await client.fetch_user(int(user_id))
                if user:
                    for story in matching_stories[:2]:
                        if await send_dm_to_user(user, story):
                            await asyncio.sleep(1)  # Small delay between DMs
                        else:
                            break  # Stop if DMs are forbidden
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

@client.event
async def on_ready():
    send_news_dms.start()

client.run(DISCORD_TOKEN)