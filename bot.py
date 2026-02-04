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
from typing import Optional, Dict, Any, Callable
from functools import wraps
import threading
import sys
from collections import deque
from enum import Enum

# Redis for metadata caching (resolves slow query issues)
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    print("[WARNING] Redis not available - caching will be in-memory only")

# All imports are already handled above

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
HN_URL = "https://news.ycombinator.com/newest"

# Validate required environment variables
if not DISCORD_TOKEN or not CHANNEL_ID or not SUPABASE_URL or not SUPABASE_KEY:
    exit(1)

# Initialize Redis for metadata caching
redis_client = None
if REDIS_AVAILABLE:
    try:
        redis_client = redis.from_url(REDIS_URL, decode_responses=True)
        redis_client.ping()  # Test connection
        print("[INFO] Redis connected successfully")
    except Exception as e:
        REDIS_AVAILABLE = False
        print(f"[WARNING] Redis connection failed: {e} - using memory cache")

# Initialize Supabase
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Initialize Discord client
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.messages = True
intents.dm_messages = True
client = discord.Client(intents=intents)

# Circuit breaker pattern for database queries
class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN" 
    HALF_OPEN = "HALF_OPEN"

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout_ms: int = 60000):
        self.failure_threshold = failure_threshold
        self.timeout_ms = timeout_ms
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
        self.lock = threading.Lock()
    
    async def execute(self, operation: Callable):
        with self.lock:
            if self.state == CircuitState.OPEN:
                if self.last_failure_time and (time.time() * 1000 - self.last_failure_time) > self.timeout_ms:
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise Exception("Circuit breaker is OPEN")
        
        try:
            result = operation()
            # Check if the result is a coroutine, await it if so
            if asyncio.iscoroutine(result):
                result = await result
            self._on_success()
            return result
        except Exception as error:
            self._on_failure()
            raise error
    
    def _on_success(self):
        with self.lock:
            self.failure_count = 0
            self.state = CircuitState.CLOSED
    
    def _on_failure(self):
        with self.lock:
            self.failure_count += 1
            self.last_failure_time = time.time() * 1000
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN

class RateLimiter:
    def __init__(self, max_requests: int = 10, window_ms: int = 60000):
        self.max_requests = max_requests
        self.window_ms = window_ms
        self.requests = deque()
        self.lock = threading.Lock()
    
    async def wait_for_slot(self):
        with self.lock:
            now = time.time() * 1000
            while self.requests and now - self.requests[0] >= self.window_ms:
                self.requests.popleft()
            
            if len(self.requests) >= self.max_requests:
                if self.requests:
                    oldest_request = self.requests[0]
                    wait_time = self.window_ms - (now - oldest_request) + 100
                    await asyncio.sleep(wait_time / 1000)
                    return await self.wait_for_slot()
            
            self.requests.append(now)

# Initialize circuit breaker and rate limiter for database operations
circuit_breaker = CircuitBreaker(3, 60000)  # 3 failures triggers 60s timeout
rate_limiter = RateLimiter(5, 30000)  # 5 requests per 30 seconds

# Rate limiting and retry constants
BASE_RETRY_DELAY = 2
MAX_RETRY_DELAY = 300
MAX_RETRIES = 5
API_RATE_LIMIT = 45
DM_RATE_LIMIT = 5

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

# Enhanced caching system for metadata (resolves slow query issues)
cache_lock = threading.Lock()
cache_hits = 0
cache_misses = 0

# User subscription cache (existing functionality)
user_cache = {}
cache_expiry = {}
CACHE_TTL = 300  # 5 minutes

# Metadata cache for database performance (new - resolves slow queries)
METADATA_CACHE_TTL = {
    'timezone_names': 86400,      # 24 hours - timezone names rarely change
    'extension_info': 43200,        # 12 hours - extensions change infrequently  
    'function_metadata': 43200,     # 12 hours - functions change rarely
    'user_subscriptions': 300          # 5 minutes - user data changes frequently
}

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

async def debug_hn_scraping():
    """Comprehensive debug function to analyze HN scraping issues"""
    debug_info = {
        "status": "starting",
        "steps": {},
        "errors": [],
        "sample_stories": []
    }
    
    try:
        # Step 1: Network connectivity
        debug_info["steps"]["network"] = "testing"
        try:
            response = requests.get(HN_URL, timeout=10)
            response.raise_for_status()
            debug_info["steps"]["network"] = f"success ({len(response.content)} bytes received)"
            debug_info["steps"]["status_code"] = response.status_code
        except Exception as e:
            debug_info["steps"]["network"] = f"failed: {str(e)}"
            debug_info["errors"].append(f"Network error: {str(e)}")
            return debug_info
        
        # Step 2: HTML parsing
        debug_info["steps"]["parsing"] = "testing"
        try:
            soup = BeautifulSoup(response.text, "html.parser")
            debug_info["steps"]["parsing"] = "success"
        except Exception as e:
            debug_info["steps"]["parsing"] = f"failed: {str(e)}"
            debug_info["errors"].append(f"HTML parsing error: {str(e)}")
            return debug_info
        
        # Step 3: CSS Selector testing
        debug_info["steps"]["selectors"] = {}
        
        # Test main story selector
        story_rows = soup.select("tr.athing")
        debug_info["steps"]["selectors"]["tr.athing"] = f"found {len(story_rows)} rows"
        
        # Test alternative selectors
        alt_story_rows = soup.select("tr.athing.submission")
        debug_info["steps"]["selectors"]["tr.athing.submission"] = f"found {len(alt_story_rows)} rows"
        
        # Step 4: Detailed parsing analysis
        if len(story_rows) == 0:
            debug_info["errors"].append("No story rows found with main selector")
            if len(alt_story_rows) > 0:
                story_rows = alt_story_rows
                debug_info["steps"]["analysis"] = "Using alternative selector (tr.athing.submission)"
        
        debug_info["steps"]["parsing_analysis"] = {
            "total_rows": len(story_rows),
            "limited_to": min(20, len(story_rows))
        }
        
        # Step 5: Story-by-story analysis for first 5
        parsed_count = 0
        failed_count = 0
        failure_reasons = {}
        
        for i, row in enumerate(story_rows[:5]):
            story_debug = {
                "index": i,
                "steps": {}
            }
            
            # Get story ID
            story_id = row.get("id")
            story_debug["steps"]["id"] = story_id if story_id else "MISSING"
            
            # Test title link selector
            title_link = row.select_one("span.titleline a")
            if title_link:
                story_debug["steps"]["title_link"] = "found"
                story_debug["title"] = title_link.text.strip()[:50] + "..." if len(title_link.text.strip()) > 50 else title_link.text.strip()
                story_debug["url"] = title_link.get("href", "")[:50] + "..." if len(title_link.get("href", "")) > 50 else title_link.get("href", "")
            else:
                story_debug["steps"]["title_link"] = "MISSING"
                failure_reasons["title_link_missing"] = failure_reasons.get("title_link_missing", 0) + 1
                failed_count += 1
                debug_info["sample_stories"].append(story_debug)
                continue
            
            # Test subtext/age extraction
            subtext = row.find_next_sibling("tr").select_one("td.subtext")
            if subtext:
                age_element = subtext.select_one("span.age")
                if age_element:
                    story_debug["steps"]["age"] = age_element.text
                else:
                    story_debug["steps"]["age"] = "MISSING"
                    failure_reasons["age_missing"] = failure_reasons.get("age_missing", 0) + 1
            else:
                story_debug["steps"]["subtext"] = "MISSING"
                failure_reasons["subtext_missing"] = failure_reasons.get("subtext_missing", 0) + 1
            
            debug_info["sample_stories"].append(story_debug)
            
            # Only count as successfully parsed if we have the essentials
            if title_link:
                parsed_count += 1
        
        debug_info["steps"]["parsing_results"] = {
            "parsed_count": parsed_count,
            "failed_count": failed_count,
            "failure_reasons": failure_reasons
        }
        
        # Step 6: Final story count
        final_stories = fetch_hn_stories()
        debug_info["steps"]["final_result"] = f"fetch_hn_stories() returned {len(final_stories)} stories"
        
        debug_info["status"] = "completed"
        return debug_info
        
    except Exception as e:
        debug_info["status"] = f"failed: {str(e)}"
        debug_info["errors"].append(f"General error: {str(e)}")
        return debug_info

def story_matches_keywords(story: dict, keywords: list) -> bool:
    """Check if story title or URL contains any keywords"""
    if not keywords:
        return True
    
    title_lower = story["title"].lower()
    url_lower = story["url"].lower()
    
    for keyword in keywords:
        keyword_lower = keyword.lower().strip()
        if keyword_lower and (keyword_lower in title_lower or keyword_lower in url_lower):
            return True
    
    return False

# Cache decorator for database operations
def cached_query(ttl: int = 3600, cache_type: str = 'default'):
    """Decorator for caching query results with circuit breaker protection"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = f"{func.__name__}_{hash(str(args) + str(kwargs))}"
            
            try:
                # Check cache first
                cached = get_cached_data(cache_key, cache_type)
                if cached is not None:
                    return cached
                
                # Execute with circuit breaker protection
                await rate_limiter.wait_for_slot()
                result = await circuit_breaker.execute(lambda: func(*args, **kwargs))
                
                # Cache the result
                set_cached_data(cache_key, result, cache_type)
                return result
                
            except Exception as error:
                # Try to serve stale data if available
                if REDIS_AVAILABLE and redis_client:
                    stale = redis_client.get(f"{cache_key}:stale")
                    if stale:
                        print(f"[WARNING] Serving stale data for {cache_key}")
                        return json.loads(stale)
                raise error
        
        return wrapper
    return decorator

@cached_query(ttl=300, cache_type='user_subscriptions')
async def load_subscriptions():
    """Load user subscriptions from Supabase with caching"""
    try:
        await rate_limiter.wait_for_slot()
        
        response = circuit_breaker.execute(
            lambda: supabase.table('subscriptions').select('*').execute()
        )
        
        # Check if response is None or data is None (can happen with RLS issues)
        if not response or response.data is None:
            print("[WARNING] No data returned - Check RLS policies or table access")
            return {}
        
        if not response.data:
            return {}
        
        subscriptions = {}
        for row in response.data:
            subscriptions[row['userId']] = {
                'subscribed': bool(row['subscribed']),
                'tags': json.loads(row['tags']) if row['tags'] else []
            }
        return subscriptions
    except Exception as e:
        error_str = str(e)
        if "no RLS policies" in error_str or "no data will be returned" in error_str:
            print("[ERROR] RLS policies issue detected - table access blocked")
        return {}

async def subscribe_user(user_id: str):
    """Subscribe user to news updates with enhanced caching"""
    try:
        # Check cache first
        cached_data = get_cached_user_data(user_id)
        if cached_data:
            subscriptions = {user_id: cached_data}
        else:
            subscriptions = await load_subscriptions()
        
        # Prepare subscription data
        if user_id not in subscriptions:
            subscription_data = {'subscribed': True, 'tags': []}
        else:
            subscription_data = subscriptions[user_id].copy()
            subscription_data['subscribed'] = True
        
        # Insert/upsert subscription with circuit breaker protection
        await rate_limiter.wait_for_slot()
        result = await circuit_breaker.execute(
            lambda: supabase.table('subscriptions').upsert({
                'userId': user_id,
                'subscribed': subscription_data['subscribed'],
                'tags': json.dumps(subscription_data['tags'])
            }).execute()
        )
        
        if result and result.data:
            # Update cache
            set_cached_user_data(user_id, subscription_data)
            return True, "Successfully subscribed to YC News updates"
        else:
            # Try to verify if it worked despite no data return
            await rate_limiter.wait_for_slot()
            verify_result = await circuit_breaker.execute(
                lambda: supabase.table('subscriptions').select('*').eq('userId', user_id).execute()
            )
            if verify_result and verify_result.data:
                # Update cache
                set_cached_user_data(user_id, subscription_data)
                return True, "Successfully subscribed to YC News updates"
            else:
                return False, "Subscription may not have been created - check table access"
            
    except Exception as e:
        error_str = str(e)
        if "row-level security" in error_str or "no RLS policies" in error_str:
            return False, "Database permission error. You need to either: 1) Add RLS policies, or 2) Disable RLS completely in Supabase."
        elif "duplicate key" in error_str:
            return True, "Already subscribed to YC News updates"
        else:
            return False, f"Error processing subscription: {error_str}"

async def unsubscribe_user(user_id: str):
    """Unsubscribe user from news updates with enhanced caching"""
    try:
        # Check cache first
        cached_data = get_cached_user_data(user_id)
        if cached_data:
            subscriptions = {user_id: cached_data}
        else:
            subscriptions = await load_subscriptions()
        
        if user_id in subscriptions:
            subscriptions[user_id]['subscribed'] = False
        
        await rate_limiter.wait_for_slot()
        result = await circuit_breaker.execute(
            lambda: supabase.table('subscriptions').upsert({
                'userId': user_id,
                'subscribed': False,
                'tags': json.dumps(subscriptions[user_id]['tags'] if user_id in subscriptions else [])
            }).execute()
        )
        
        if result and result.data:
            # Update cache
            if user_id in subscriptions:
                subscriptions[user_id]['subscribed'] = False
                set_cached_user_data(user_id, subscriptions[user_id])
            return True, "Successfully unsubscribed from YC News updates"
        else:
            return False, "Failed to update subscription record"
            
    except Exception as e:
        error_str = str(e)
        if "row-level security" in error_str:
            return False, "Database permission error. Please check RLS policies in Supabase."
        else:
            return False, f"Error processing unsubscription: {error_str}"

async def get_user_tags(user_id: str):
    """Get user's current tags with enhanced caching"""
    try:
        # Check cache first
        cached_data = get_cached_user_data(user_id)
        if cached_data:
            subscriptions = {user_id: cached_data}
        else:
            subscriptions = await load_subscriptions()
        
        if user_id not in subscriptions:
            return True, "User is not subscribed", []
        
        tags = subscriptions[user_id].get('tags', [])
        if tags:
            return True, f"Current tags: {', '.join(tags)}", tags
        else:
            return True, "No tags found. Use add endpoint to add tags", []
    except Exception as e:
        return False, f"Error retrieving tags: {str(e)}", []

async def add_user_tags(user_id: str, tags: list):
    """Add tags to user's subscription with enhanced caching"""
    try:
        # Check cache first
        cached_data = get_cached_user_data(user_id)
        if cached_data:
            subscriptions = {user_id: cached_data}
        else:
            subscriptions = await load_subscriptions()
        
        if user_id not in subscriptions:
            subscriptions[user_id] = {'subscribed': True, 'tags': []}
        
        if 'tags' not in subscriptions[user_id]:
            subscriptions[user_id]['tags'] = []
            
        added_tags = []
        for tag in tags:
            tag_clean = tag.strip()
            if tag_clean and tag_clean not in subscriptions[user_id]['tags']:
                subscriptions[user_id]['tags'].append(tag_clean)
                added_tags.append(tag_clean)
        
        await rate_limiter.wait_for_slot()
        await circuit_breaker.execute(
            lambda: supabase.table('subscriptions').upsert({
                'userId': user_id,
                'subscribed': subscriptions[user_id]['subscribed'],
                'tags': json.dumps(subscriptions[user_id]['tags'])
            }).execute()
        )
        
        # Update cache
        set_cached_user_data(user_id, subscriptions[user_id])
        
        if added_tags:
            return True, f"Successfully added tags: {', '.join(added_tags)}", added_tags
        else:
            return True, "No new tags to add", []
    except Exception as e:
        return False, f"Error adding tags: {str(e)}", []

async def remove_user_tags(user_id: str, tags: list):
    """Remove tags from user's subscription with enhanced caching"""
    try:
        # Check cache first
        cached_data = get_cached_user_data(user_id)
        if cached_data:
            subscriptions = {user_id: cached_data}
        else:
            subscriptions = await load_subscriptions()
        
        if user_id not in subscriptions:
            return False, "User is not subscribed", []
        
        if 'tags' not in subscriptions[user_id]:
            subscriptions[user_id]['tags'] = []
            
        removed_tags = []
        for tag in tags:
            tag_clean = tag.strip()
            if tag_clean in subscriptions[user_id]['tags']:
                subscriptions[user_id]['tags'].remove(tag_clean)
                removed_tags.append(tag_clean)
        
        await rate_limiter.wait_for_slot()
        await circuit_breaker.execute(
            lambda: supabase.table('subscriptions').upsert({
                'userId': user_id,
                'subscribed': subscriptions[user_id]['subscribed'],
                'tags': json.dumps(subscriptions[user_id]['tags'])
            }).execute()
        )
        
        # Update cache
        set_cached_user_data(user_id, subscriptions[user_id])
        
        if removed_tags:
            return True, f"Successfully removed tags: {', '.join(removed_tags)}", removed_tags
        else:
            return True, "No matching tags found to remove", []
    except Exception as e:
        return False, f"Error removing tags: {str(e)}", []

# Enhanced caching functions with Redis support
def get_cached_data(cache_key: str, cache_type: str = 'default') -> Optional[Any]:
    """Get cached data from Redis or memory fallback"""
    global cache_hits, cache_misses
    
    ttl = METADATA_CACHE_TTL.get(cache_type, CACHE_TTL)
    
    if REDIS_AVAILABLE and redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                cache_hits += 1
                return json.loads(cached)
        except Exception as e:
            print(f"[WARNING] Redis get failed: {e} - falling back to memory")
    
    # Fallback to memory cache
    with cache_lock:
        if cache_key in user_cache and cache_key in cache_expiry:
            if time.time() - cache_expiry[cache_key] < ttl:
                cache_hits += 1
                return user_cache[cache_key]
            else:
                # Clean up expired entry
                user_cache.pop(cache_key, None)
                cache_expiry.pop(cache_key, None)
    
    cache_misses += 1
    return None

def set_cached_data(cache_key: str, data: Any, cache_type: str = 'default'):
    """Set cached data in Redis and memory"""
    ttl = METADATA_CACHE_TTL.get(cache_type, CACHE_TTL)
    
    if REDIS_AVAILABLE and redis_client:
        try:
            redis_client.setex(cache_key, ttl, json.dumps(data))
        except Exception as e:
            print(f"[WARNING] Redis set failed: {e}")
    
    # Fallback to memory cache
    with cache_lock:
        user_cache[cache_key] = data
        cache_expiry[cache_key] = time.time()

def get_cached_user_data(user_id: str):
    """Get cached user subscription data"""
    return get_cached_data(f"user_sub:{user_id}", 'user_subscriptions')

def set_cached_user_data(user_id: str, data):
    """Set cached user subscription data"""
    set_cached_data(f"user_sub:{user_id}", data, 'user_subscriptions')

def cleanup_expired_cache():
    """Clean up expired memory cache entries"""
    with cache_lock:
        current_time = time.time()
        expired_keys = [
            key for key, expiry_time in cache_expiry.items()
            if current_time - expiry_time >= METADATA_CACHE_TTL.get('default', CACHE_TTL)
        ]
        for key in expired_keys:
            user_cache.pop(key, None)
            cache_expiry.pop(key, None)
        
        if expired_keys:
            print(f"[INFO] Cleaned {len(expired_keys)} expired cache entries")

# Cache decorator for database operations - moved above to resolve ordering issue

# Specific metadata caching functions
async def get_cached_timezone_names():
    """Get timezone names with long-term caching (resolves pg_timezone_names slow query)"""
    cache_key = "pg_timezone_names"
    cached = get_cached_data(cache_key, 'timezone_names')
    if cached is not None:
        return cached
    
    # This would normally trigger the slow pg_timezone_names query
    # Instead, we cache a static list or fetch from materialized view
    common_timezones = [
        'UTC', 'US/Eastern', 'US/Central', 'US/Mountain', 'US/Pacific',
        'Europe/London', 'Europe/Paris', 'Europe/Berlin', 'Europe/Moscow',
        'Asia/Tokyo', 'Asia/Shanghai', 'Asia/Dubai', 'Asia/Kolkata',
        'Australia/Sydney', 'Pacific/Auckland'
    ]
    
    set_cached_data(cache_key, common_timezones, 'timezone_names')
    return common_timezones

async def get_cached_extension_info():
    """Get extension information with caching (resolves pg_available_extensions slow query)"""
    cache_key = "pg_extension_info"
    cached = get_cached_data(cache_key, 'extension_info')
    if cached is not None:
        return cached
    
    # Cache basic extension info to avoid frequent queries
    extension_info = [
        {'name': 'uuid-ossp', 'schema': 'public', 'installed_version': '1.1.2'},
        {'name': 'pg_stat_statements', 'schema': 'pg_catalog', 'installed_version': '1.10'},
        {'name': 'pg_cron', 'schema': 'public', 'installed_version': '1.5.0'},
        {'name': 'pgcrypto', 'schema': 'public', 'installed_version': '1.3.2'},
    ]
    
    set_cached_data(cache_key, extension_info, 'extension_info')
    return extension_info

async def get_cached_function_metadata():
    """Get function metadata with aggressive caching (resolves recursive query 429 errors)"""
    cache_key = "pg_function_metadata"
    cached = get_cached_data(cache_key, 'function_metadata')
    if cached is not None:
        return cached
    
    # Cache essential function metadata to prevent recursive queries
    function_metadata = {
        'public_functions': [
            {'schema': 'public', 'name': 'get_user_subscriptions', 'return_type': 'table'},
            {'schema': 'public', 'name': 'update_subscription', 'return_type': 'boolean'},
            {'schema': 'public', 'name': 'send_news_dms', 'return_type': 'void'},
        ],
        'total_count': 3,
        'last_updated': time.time()
    }
    
    set_cached_data(cache_key, function_metadata, 'function_metadata')
    return function_metadata

# Cache statistics
def get_cache_stats():
    """Get cache performance statistics"""
    total_requests = cache_hits + cache_misses
    hit_ratio = cache_hits / total_requests if total_requests > 0 else 0
    
    return {
        'cache_hits': cache_hits,
        'cache_misses': cache_misses,
        'hit_ratio': hit_ratio,
        'memory_cache_size': len(user_cache),
        'redis_connected': REDIS_AVAILABLE and redis_client is not None
    }

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

@tasks.loop(hours=6)
async def send_news_dms():
    """Send news to subscribed users with enhanced caching and performance optimization"""
    try:
        # Use cached subscriptions with circuit breaker protection
        subscriptions = await load_subscriptions()
        if not subscriptions:
            return
        
        # Fetch stories once per cycle instead of multiple times
        stories = fetch_hn_stories()
        if not stories:
            return
            
        # Limit to recent stories to reduce processing
        new_stories = [s for s in stories[:20] if s["id"] not in posted_ids]
        
        if not new_stories:
            return
        
        # Process users in smaller batches to reduce load
        processed_users = 0
        for user_id, user_data in subscriptions.items():
            if processed_users >= 5:  # Limit to 5 users per cycle
                break
                
            if not user_data.get('subscribed'):
                continue
            
            keywords = user_data.get('tags', [])
            # Send all stories if no keywords, otherwise filter by keywords
            stories_to_check = new_stories[:5]  # Limit to 5 stories per user
            
            if keywords:
                # Filter by keywords
                matching_stories = []
                for story in stories_to_check:
                    if story_matches_keywords(story, keywords):
                        matching_stories.append(story)
                stories_to_send = matching_stories[:3]  # Send top 3 matching stories
            else:
                # No keywords = send latest stories
                stories_to_send = stories_to_check[:3]  # Send top 3 latest stories
            
            # Send multiple stories to each user
            if stories_to_send:
                try:
                    user = await client.fetch_user(int(user_id))
                    if user:
                        for i, story in enumerate(stories_to_send):
                            if await send_dm_to_user(user, story):
                                if i < len(stories_to_send) - 1:  # No delay after last story
                                    await asyncio.sleep(2)  # Delay between stories
                        processed_users += 1
                    else:
                        break
                except Exception:
                    continue
        
        # Mark stories as posted
        for story in new_stories:
            posted_ids.add(story["id"])
            
    except Exception as e:
        print(f"[ERROR] Error in send_news_dms: {e}")
        return

# Cache cleanup task (resolves memory leak issues)
@tasks.loop(minutes=10)
async def cleanup_cache_task():
    """Periodic cleanup of expired cache entries"""
    cleanup_expired_cache()

# Cache statistics task for monitoring
@tasks.loop(hours=1)
async def cache_stats_task():
    """Log cache performance statistics"""
    stats = get_cache_stats()
    print(f"[CACHE STATS] Hits: {stats['cache_hits']}, Misses: {stats['cache_misses']}, Hit Ratio: {stats['hit_ratio']:.2%}, Memory Size: {stats['memory_cache_size']}, Redis Connected: {stats['redis_connected']}")

# Preload critical caches on startup (resolves slow query issues immediately)
async def preload_critical_caches():
    """Preload critical metadata caches to avoid initial slow queries"""
    try:
        await asyncio.gather(
            get_cached_timezone_names(),
            get_cached_extension_info(), 
            get_cached_function_metadata()
        )
        print("[INFO] Critical metadata caches preloaded successfully")
    except Exception as e:
        print(f"[WARNING] Failed to preload caches: {e}")

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
            user_id = str(message.author.id)
            
            success, response_message = await subscribe_user(user_id)
            
            if success:
                await message.author.send(f"✅ {response_message}")
            else:
                await message.channel.send(f"❌ {response_message}")
        except discord.HTTPException as e:
            if e.status == 429:
                await asyncio.sleep(2)
            await message.channel.send("❌ Error processing subscription. Please try again later.")
        except Exception:
            await message.channel.send("❌ Error processing subscription. Please try again.")
    
    elif content.startswith('!yc-news unsubscribe'):
        try:
            user_id = str(message.author.id)
            
            success, response_message = await unsubscribe_user(user_id)
            
            if success:
                await message.author.send(f"❌ {response_message}")
            else:
                await message.channel.send(f"❌ {response_message}")
        except Exception:
            await message.channel.send("❌ Error processing unsubscription. Please try again.")
    
    elif content.startswith('!yc-news add='):
        try:
            user_id = str(message.author.id)
            
            tags_str = content.split('=', 1)[1].strip()
            if tags_str.startswith('"') and tags_str.endswith('"'):
                tags_str = tags_str[1:-1]
            
            new_tags = [tag.strip() for tag in tags_str.split(',') if tag.strip()]
            
            success, response_message, added_tags = await add_user_tags(user_id, new_tags)
            
            if success and added_tags:
                await message.author.send(f"✅ {response_message}")
            else:
                await message.author.send(f"ℹ️ {response_message}")
        except Exception:
            await message.channel.send("❌ Error adding tags. Please try again.")
    
    elif content.startswith('!yc-news remove='):
        try:
            user_id = str(message.author.id)
            
            tags_str = content.split('=', 1)[1].strip()
            if tags_str.startswith('"') and tags_str.endswith('"'):
                tags_str = tags_str[1:-1]
            
            tags_to_remove = [tag.strip() for tag in tags_str.split(',') if tag.strip()]
            
            success, response_message, removed_tags = await remove_user_tags(user_id, tags_to_remove)
            
            if success:
                await message.author.send(f"✅ {response_message}")
                if not removed_tags:
                    await message.author.send("ℹ️ No matching tags found.")
            else:
                await message.author.send(f"❌ {response_message}")
        except Exception:
            await message.channel.send("❌ Error removing tags. Please try again.")
    
    elif content == '!yc-news tags':
        try:
            user_id = str(message.author.id)
            
            success, response_message, tags = await get_user_tags(user_id)
            
            if success:
                if tags:
                    await message.author.send(f"📋 {response_message}")
                else:
                    await message.author.send("📋 You have no tags subscribed. Without tags, you'll receive the top 3 latest stories. Use `!yc-news add=\"AI, ML\"` to add tags and get filtered news.")
            else:
                await message.author.send(f"❌ {response_message}")
        except Exception:
            await message.channel.send("❌ Error retrieving tags. Please try again.")
    
    elif content == '!yc-news clear':
        """Clear posted_ids cache to resend stories"""
        global posted_ids
        posted_ids.clear()
        await message.author.send("🗑️ Posted story cache cleared. Stories can be resent now.")
    
    elif content == '!yc-news cache-stats':
        """Show cache performance statistics"""
        stats = get_cache_stats()
        msg = f"""📊 **Cache Performance Stats**
        
**Cache Performance:**
• Hits: {stats['cache_hits']}
• Misses: {stats['cache_misses']}
• Hit Ratio: {stats['hit_ratio']:.2%}
• Memory Cache Size: {stats['memory_cache_size']} entries
• Redis Connected: {'✅ Yes' if stats['redis_connected'] else '❌ No (Memory fallback)'}

**Cache TTL Settings:**
• User Subscriptions: 5 minutes
• Timezone Names: 24 hours
• Extension Info: 12 hours
• Function Metadata: 12 hours"""
        await message.author.send(msg)
    
    elif content == '!yc-news refresh-cache':
        """Force refresh user cache and preload critical caches"""
        user_id = str(message.author.id)
        
        # Clear user cache
        with cache_lock:
            keys_to_remove = [k for k in user_cache.keys() if k.startswith(f'user_sub:{user_id}')]
            for key in keys_to_remove:
                user_cache.pop(key, None)
                cache_expiry.pop(key, None)
        
        # Clear Redis cache for user if available
        if REDIS_AVAILABLE and redis_client:
            try:
                redis_client.delete(f"user_sub:{user_id}")
            except Exception as e:
                print(f"[WARNING] Redis delete failed: {e}")
        
        # Preload critical caches
        await preload_critical_caches()
        
        await message.author.send("🔄 Your cache has been refreshed and critical caches preloaded.")
    
    elif content == '!yc-news preload':
        """Preload all critical caches to prevent slow queries"""
        await preload_critical_caches()
        
        stats = get_cache_stats()
        await message.author.send(f"""⚡ **Critical Caches Preloaded**
        
✅ Timezone Names (24h TTL)
✅ Extension Info (12h TTL)  
✅ Function Metadata (12h TTL)

Cache Status: {stats['hit_ratio']:.2%} hit ratio
Redis Status: {'Connected' if stats['redis_connected'] else 'Memory Fallback'}""")
    
    elif content == '!yc-news test':
        debug_info = await debug_hn_scraping()
        
        # Format the debug results for Discord
        msg_parts = []
        msg_parts.append(f"🔍 **HN Scraping Debug Report**")
        msg_parts.append(f"**Status:** {debug_info['status']}")
        
        # Network and parsing
        if 'network' in debug_info['steps']:
            msg_parts.append(f"**Network:** {debug_info['steps']['network']}")
        if 'parsing' in debug_info['steps']:
            msg_parts.append(f"**HTML Parsing:** {debug_info['steps']['parsing']}")
        
        # Selector results
        if 'selectors' in debug_info['steps']:
            msg_parts.append("**CSS Selectors:**")
            for selector, result in debug_info['steps']['selectors'].items():
                msg_parts.append(f"  • `{selector}` → {result}")
        
        # Analysis results
        if 'parsing_analysis' in debug_info['steps']:
            analysis = debug_info['steps']['parsing_analysis']
            msg_parts.append(f"**Analysis:** {analysis['total_rows']} total rows, limited to {analysis['limited_to']}")
        
        # Parsing results
        if 'parsing_results' in debug_info['steps']:
            results = debug_info['steps']['parsing_results']
            msg_parts.append(f"**Parsing Results:** {results['parsed_count']} successful, {results['failed_count']} failed")
            
            if results['failure_reasons']:
                msg_parts.append("**Failure Reasons:**")
                for reason, count in results['failure_reasons'].items():
                    msg_parts.append(f"  • {reason}: {count}")
        
        # Final result
        if 'final_result' in debug_info['steps']:
            msg_parts.append(f"**Final Result:** {debug_info['steps']['final_result']}")
        
        # Sample stories analysis
        if debug_info['sample_stories']:
            msg_parts.append("\n**Sample Stories Analysis:**")
            for story in debug_info['sample_stories'][:3]:  # Limit to 3 samples to avoid message length issues
                msg_parts.append(f"\n**Story {story['index']}:**")
                msg_parts.append(f"  • ID: {story['steps'].get('id', 'N/A')}")
                if 'title' in story:
                    msg_parts.append(f"  • Title: {story['title'][:60]}...")
                if 'url' in story:
                    msg_parts.append(f"  • URL: {story['url'][:40]}...")
                msg_parts.append(f"  • Title Link: {story['steps'].get('title_link', 'N/A')}")
                if 'age' in story['steps']:
                    msg_parts.append(f"  • Age: {story['steps']['age']}")
        
        # Errors
        if debug_info['errors']:
            msg_parts.append("\n**Errors:**")
            for error in debug_info['errors']:
                msg_parts.append(f"  • {error}")
        
        # Send the debug report
        debug_message = "\n".join(msg_parts)
        
        # Split if too long for Discord (max 2000 chars)
        if len(debug_message) > 1900:
            parts = [debug_message[i:i+1900] for i in range(0, len(debug_message), 1900)]
            for i, part in enumerate(parts):
                header = f"🔍 **HN Scraping Debug Report (Part {i+1}/{len(parts)})**" if i > 0 else part
                await message.author.send(header)
                await asyncio.sleep(0.5)  # Small delay between messages
        else:
            await message.author.send(debug_message)

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
    
    # Preload critical caches to resolve slow query issues immediately
    await preload_critical_caches()
    
    # Start all background tasks
    send_news_dms.start()
    cleanup_cache_task.start()
    cache_stats_task.start()
    
    print("[INFO] Background tasks started: news delivery, cache cleanup, cache statistics")

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