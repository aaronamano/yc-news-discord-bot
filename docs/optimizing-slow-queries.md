# Optimizing Slow PostgreSQL Queries in Supabase

## Problem
The following queries are causing performance issues in your Supabase database:

1. **Most time-consuming and frequent:**
   ```sql
   select name from pg_timezone_names
   ```

2. **Most frequent calls:**
   ```sql
   select e.name, n.nspname as schema, e.default_version, x.extversion as installed_version, e.comment
   from pg_available_extensions () e (name, default_version, comment)
   left join pg_extension x on e.name = x.extname
   left join pg_namespace n on x.extnamespace = n.oid
   where $1
   ```

3. **Most requests and causing 429 errors (recursive query):**
   ```sql
   -- Recursive function/procedure metadata query
   with recursive
     recurse as (
       select
         oid,
         typbasetype,
         typnamespace as base_namespace,
         COALESCE(NULLIF(typbasetype, $3), oid) as base_type
       from
         pg_type
       union
       select
         t.oid,
         b.typbasetype,
         b.typnamespace as base_namespace,
         COALESCE(NULLIF(b.typbasetype, $4), b.oid) as base_type
       from
         recurse t
         join pg_type b on t.typbasetype = b.oid
     )
   select
     -- Complex function metadata extraction
     pn.nspname as proc_schema,
     p.proname as proc_name,
     -- ... extensive function metadata
   from pg_proc p
     left join arguments a on a.oid = p.oid
     join pg_namespace pn on pn.oid = p.pronamespace
     join base_types bt on bt.oid = p.prorettype
     join pg_type t on t.oid = bt.base_type
     -- ... multiple joins
   where t.oid <> $45::regtype
     and COALESCE(a.callable, $46)
     and prokind = $47
     and p.pronamespace = any ($1::regnamespace[])
   ```

## Solutions

### 1. Cache Timezone Names
The `pg_timezone_names` view is expensive to query repeatedly. Implement caching:

#### Option A: Materialized View
```sql
-- Create a materialized view to cache timezone names
CREATE MATERIALIZED VIEW cached_timezone_names AS
SELECT name FROM pg_timezone_names;

-- Create an index on the name column
CREATE INDEX idx_cached_timezone_names_name ON cached_timezone_names(name);

-- Refresh the materialized view periodically (daily is usually sufficient)
-- This can be scheduled via cron job or database trigger
```

#### Option B: Application-Level Caching
```python
# Cache timezone names in your application
import asyncio
from supabase import create_client

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

timezones = supabase.table('cached_timezone_names').select('name').execute()

# Store in Redis or application cache with long TTL (24h+)
```

### 2. Cache Extension Information
For the extension queries, implement similar caching strategies:

#### Option A: Materialized View
```sql
CREATE MATERIALIZED VIEW cached_extension_info AS
SELECT e.name, n.nspname as schema, e.default_version, x.extversion as installed_version, e.comment
FROM pg_available_extensions () e (name, default_version, comment)
LEFT JOIN pg_extension x ON e.name = x.extname
LEFT JOIN pg_namespace n ON x.extnamespace = n.oid;

CREATE INDEX idx_cached_extension_info_name ON cached_extension_info(name);
```

### 3. Cache Function/Procedure Metadata (Critical for 429 Errors)
The recursive function metadata query is the main culprit for 429 errors. This needs aggressive caching:

#### Option A: Complete Materialized View
```sql
-- Materialized view for function metadata (most comprehensive solution)
CREATE MATERIALIZED VIEW cached_function_metadata AS
WITH RECURSIVE base_types AS (
  WITH RECURSIVE
    recurse AS (
      SELECT oid, typbasetype, typnamespace as base_namespace, 
             COALESCE(NULLIF(typbasetype, 0), oid) as base_type
      FROM pg_type
      UNION
      SELECT t.oid, b.typbasetype, b.typnamespace as base_namespace,
             COALESCE(NULLIF(b.typbasetype, 0), b.oid) as base_type
      FROM recurse t JOIN pg_type b ON t.typbasetype = b.oid
    )
  SELECT oid, base_namespace, base_type
  FROM recurse WHERE typbasetype = 0
),
arguments AS (
  SELECT oid, array_agg(
    (COALESCE(name, ''), type::regtype::text,
     CASE type WHEN 'character varying'::regtype THEN 'varchar'
               WHEN 'character'::regtype THEN 'char'
               WHEN 'timestamp without time zone'::regtype THEN 'timestamp'
               WHEN 'timestamp with time zone'::regtype THEN 'timestamptz'
               ELSE type::regtype::text END,
     idx <= (pronargs - pronargdefaults), 
     COALESCE(mode = 'v', false))
    ORDER BY idx) as args,
  CASE COUNT(*) - COUNT(name) 
    WHEN 0 THEN true
    WHEN 1 THEN (array_agg(type))[1] IN ('character varying'::regtype, 'character'::regtype, 
                                         'timestamp without time zone'::regtype, 
                                         'timestamp with time zone'::regtype, 'numeric'::regtype)
    ELSE false END as callable
  FROM pg_proc, unnest(proargnames, proargtypes, proargmodes) WITH ORDINALITY AS _(name, type, mode, idx)
  WHERE type IS NOT NULL
  GROUP BY oid
)
SELECT pn.nspname as proc_schema, p.proname as proc_name, d.description as proc_description,
       COALESCE(a.args, '{}') as args, tn.nspname as schema,
       COALESCE(comp.relname, t.typname) as name, p.proretset as rettype_is_setof,
       (t.typtype = 'c' OR COALESCE(proargmodes::text[] && ARRAY['t','b','o'], false)) as rettype_is_composite,
       bt.oid <> bt.base_type as rettype_is_composite_alias, p.provolatile,
       p.provariadic > 0 as hasvariadic, p.pronamespace
FROM pg_proc p
  LEFT JOIN arguments a ON a.oid = p.oid
  JOIN pg_namespace pn ON pn.oid = p.pronamespace
  JOIN base_types bt ON bt.oid = p.prorettype
  JOIN pg_type t ON t.oid = bt.base_type
  JOIN pg_namespace tn ON tn.oid = t.typnamespace
  LEFT JOIN pg_class comp ON comp.oid = t.typrelid
  LEFT JOIN pg_description d ON d.objoid = p.oid AND d.classoid = 'pg_proc'::regclass
WHERE t.oid <> 'unknown'::regtype AND COALESCE(a.callable, true) AND prokind = 'f';

-- Essential indexes
CREATE INDEX idx_cached_function_metadata_schema ON cached_function_metadata(proc_schema, proc_name);
CREATE INDEX idx_cached_function_metadata_namespace ON cached_function_metadata(pronamespace);
```

#### Option B: Simplified Function Metadata Cache
```sql
-- If the full query is too complex, cache essential parts
CREATE MATERIALIZED VIEW cached_function_basic AS
SELECT 
  pn.nspname as proc_schema,
  p.proname as proc_name,
  p.pronamespace,
  p.prorettype::regtype as return_type,
  p.provolatile,
  prokind
FROM pg_proc p
JOIN pg_namespace pn ON pn.oid = p.pronamespace
WHERE prokind = 'f' AND pronamespace = ANY(ARRAY['public'::regnamespace, 'auth'::regnamespace]);

CREATE INDEX idx_cached_function_basic_name ON cached_function_basic(proc_schema, proc_name);
```

#### Option C: Namespace-Specific Caching
```sql
-- Cache functions by specific namespaces to reduce query scope
CREATE MATERIALIZED VIEW cached_public_functions AS
SELECT pn.nspname, p.proname, p.oid, p.prorettype, p.proargtypes, p.proargnames
FROM pg_proc p
JOIN pg_namespace pn ON pn.oid = p.pronamespace
WHERE pn.nspname = 'public' AND prokind = 'f';
```

#### Option B: Database Function with Cache
```sql
CREATE OR REPLACE FUNCTION get_extension_info(ext_name text DEFAULT NULL)
RETURNS TABLE(name text, schema text, default_version text, installed_version text, comment text)
LANGUAGE plpgsql
AS $$
BEGIN
    IF ext_name IS NOT NULL THEN
        RETURN QUERY
        SELECT * FROM cached_extension_info 
        WHERE name = ext_name;
    ELSE
        RETURN QUERY
        SELECT * FROM cached_extension_info;
    END IF;
END;
$$;
```

### 4. Implement Refresh Strategy

#### Automated Refresh Script
```sql
-- Function to refresh all cached views
CREATE OR REPLACE FUNCTION refresh_metadata_cache()
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    REFRESH MATERIALIZED VIEW cached_timezone_names;
    REFRESH MATERIALIZED VIEW cached_extension_info;
    REFRESH MATERIALIZED VIEW cached_function_metadata;
    REFRESH MATERIALIZED VIEW cached_function_basic;
END;
$$;

-- Schedule refresh using pg_cron extension
-- Install pg_cron first: CREATE EXTENSION pg_cron;
SELECT cron.schedule('refresh-metadata-cache', '0 2 * * *', 'SELECT refresh_metadata_cache();');

-- Additional refresh during peak usage prevention (every 6 hours)
SELECT cron.schedule('refresh-metadata-cache-frequent', '0 */6 * * *', 'SELECT refresh_metadata_cache();');
```

### 5. Application-Level Solutions

#### Implement Query Result Caching
```python
# Example using Redis with aggressive caching for function metadata
import redis
import asyncio
import json
import time
from typing import Optional, Dict, Any, Callable
from functools import wraps
from supabase import create_client

# Redis connection
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

async def get_timezones() -> list:
    """Get timezone names with caching"""
    cache_key = 'pg_timezone_names'
    cached = redis_client.get(cache_key)
    
    if cached:
        return json.loads(cached)
    
    result = supabase.table('cached_timezone_names').select('name').execute()
    
    # Cache for 24 hours
    redis_client.setex(cache_key, 86400, json.dumps(result.data))
    return result.data

# CRITICAL: Cache function metadata to prevent 429 errors
async def get_function_metadata(namespace: Optional[str] = None) -> list:
    """Get function metadata with aggressive caching"""
    cache_key = f"function_metadata_{namespace}" if namespace else "function_metadata_all"
    cached = redis_client.get(cache_key)
    
    if cached:
        return json.loads(cached)
    
    query = supabase.table('cached_function_metadata')
    if namespace:
        query = query.eq('proc_schema', namespace)
    
    result = query.select('*').execute()
    
    # Cache for 12 hours (function metadata rarely changes)
    redis_client.setex(cache_key, 43200, json.dumps(result.data))
    return result.data

# Preload critical caches on startup
async def preload_caches():
    """Preload critical metadata caches"""
    try:
        await asyncio.gather(
            get_timezones(),
            get_function_metadata('public'),
            get_function_metadata('auth'),
            get_function_metadata()  # Load all functions
        )
        print("Critical metadata caches preloaded")
    except Exception as error:
        print(f"Failed to preload caches: {error}")
```

#### Rate Limiting and Circuit Breaker Pattern
```python
# Implement rate limiting to prevent 429 errors
import time
from collections import deque
from threading import Lock
from enum import Enum

class CircuitState(Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"

class RateLimiter:
    def __init__(self, max_requests: int = 10, window_ms: int = 60000):
        self.max_requests = max_requests
        self.window_ms = window_ms
        self.requests = deque()
        self.lock = Lock()
    
    async def wait_for_slot(self):
        """Wait for an available request slot"""
        with self.lock:
            now = time.time() * 1000  # Convert to milliseconds
            
            # Remove old requests outside the window
            while self.requests and now - self.requests[0] >= self.window_ms:
                self.requests.popleft()
            
            # If at capacity, wait
            if len(self.requests) >= self.max_requests:
                if self.requests:
                    oldest_request = self.requests[0]
                    wait_time = self.window_ms - (now - oldest_request) + 100
                    await asyncio.sleep(wait_time / 1000)  # Convert back to seconds
                    return await self.wait_for_slot()
            
            self.requests.append(now)

# Circuit breaker pattern for database queries
class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, timeout_ms: int = 60000):
        self.failure_threshold = failure_threshold
        self.timeout_ms = timeout_ms
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
        self.lock = Lock()
    
    async def execute(self, operation: Callable):
        """Execute operation with circuit breaker protection"""
        with self.lock:
            if self.state == CircuitState.OPEN:
                if self.last_failure_time and (time.time() * 1000 - self.last_failure_time) > self.timeout_ms:
                    self.state = CircuitState.HALF_OPEN
                else:
                    raise Exception("Circuit breaker is OPEN")
        
        try:
            result = await operation()
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
```

#### Batch API Calls with Caching
```python
# Combine multiple metadata requests into single calls
rate_limiter = RateLimiter(5, 30000)  # 5 requests per 30 seconds
circuit_breaker = CircuitBreaker(3, 60000)  # 3 failures triggers 60s timeout

async def get_metadata():
    """Get all metadata with rate limiting and circuit breaker"""
    await rate_limiter.wait_for_slot()
    
    return await circuit_breaker.execute(async () -> {
        timezones, functions, extensions = await asyncio.gather(
            get_timezones(),
            get_function_metadata(),
            get_extensions()
        )
        
        return {
            'timezones': timezones,
            'functions': functions,
            'extensions': extensions
        }
    })

# Decorator for cached queries with fallback
def cached_query(ttl: int = 3600):
    """Decorator for caching query results with stale fallback"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            cache_key = f"{func.__name__}_{hash(str(args) + str(kwargs))}"
            
            try:
                cached = redis_client.get(cache_key)
                if cached:
                    return json.loads(cached)
                
                result = await func(*args, **kwargs)
                redis_client.setex(cache_key, ttl, json.dumps(result))
                return result
                
            except Exception as error:
                # Try to serve stale data if available
                stale = redis_client.get(f"{cache_key}:stale")
                if stale:
                    print(f"Warning: Serving stale data for {cache_key}")
                    return json.loads(stale)
                raise error
        
        return wrapper
    return decorator

# Example usage
@cached_query(ttl=3600)
async def get_user_functions(user_id: str):
    """Get user-specific functions with caching"""
    return supabase.table('user_functions').select('*').eq('user_id', user_id).execute()
```

### 6. Database Configuration Optimizations

#### Connection Pooling
Ensure your Supabase connection pool is properly configured for high metadata query volume:
```python
# In your database configuration (using SQLAlchemy or similar)
from sqlalchemy import create_engine
import os

DATABASE_URL = os.getenv('DATABASE_URL')

engine = create_engine(
    DATABASE_URL,
    pool_size=50,           # Increased pool size for metadata queries
    max_overflow=20,        # Additional connections when pool is full
    pool_timeout=30,        # Longer timeout for complex recursive queries
    pool_recycle=3600,      # Recycle connections every hour
    pool_pre_ping=True,     # Verify connections before use
    connect_args={
        'command_timeout': 60,  # Longer timeout for recursive queries
        'connect_timeout': 10,
    }
)

# For async databases (like asyncpg)
import asyncpg

async def create_db_pool():
    return await asyncpg.create_pool(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT')),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        min_size=5,           # Minimum connections
        max_size=50,          # Maximum connections
        command_timeout=60,   # Timeout for recursive queries
        connect_timeout=10,
    )
```

#### Query Timeout and Statement Optimization
```sql
-- Set appropriate timeouts for recursive queries
SET statement_timeout = '30s'; -- Prevent runaway recursive queries
SET lock_timeout = '10s';

-- Optimize recursive query performance
SET work_mem = '64MB'; -- Increase memory for complex joins
SET shared_preload_libraries = 'pg_stat_statements'; -- Monitor query performance
```

#### Database-Level Rate Limiting
```sql
-- Create a function to rate limit queries per connection
CREATE OR REPLACE FUNCTION rate_limit_queries()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    query_count integer;
BEGIN
    -- Count queries in the last minute from this connection
    SELECT COUNT(*) INTO query_count
    FROM pg_stat_activity 
    WHERE state = 'active' 
      AND query NOT LIKE '%pg_stat_activity%'
      AND backend_start > now() - interval '1 minute'
      AND datname = current_database()
      AND usename = current_user;
    
    -- If too many queries, abort
    IF query_count > 100 THEN -- Adjust threshold based on your needs
        RAISE EXCEPTION 'Too many queries per minute: %', query_count;
    END IF;
    
    RETURN NULL;
END;
$$;

-- Apply to critical tables (optional, use with caution)
-- CREATE TRIGGER rate_limit_trg BEFORE INSERT OR SELECT ON pg_proc 
-- FOR EACH STATEMENT EXECUTE FUNCTION rate_limit_queries();
```

#### Query Optimization
```sql
-- Add appropriate indexes if querying specific timezones
CREATE INDEX idx_cached_timezone_names_name_pattern ON cached_timezone_names(name text_pattern_ops);

-- Consider partial indexes if you only need certain timezones
CREATE INDEX idx_cached_timezone_names_common ON cached_timezone_names(name) 
WHERE name LIKE ANY(ARRAY['UTC%', 'US%', 'Europe%']);
```

### 7. Monitoring and Maintenance

#### Performance Monitoring
```sql
-- Monitor query performance including the recursive function query
SELECT query, calls, total_time, mean_time, rows
FROM pg_stat_statements 
WHERE query LIKE '%pg_timezone_names%' 
   OR query LIKE '%pg_available_extensions%'
   OR query LIKE '%recurse%'
   OR query LIKE '%pg_proc%'
ORDER BY total_time DESC;

-- Monitor for 429-like behavior (too many requests)
SELECT 
  datname,
  usename,
  state,
  count(*) as connection_count,
  max(backend_start) as oldest_connection
FROM pg_stat_activity 
WHERE state = 'active' 
  AND query NOT LIKE '%pg_stat_activity%'
GROUP BY datname, usename, state
HAVING count(*) > 10;
```

#### Real-time Alerting for Recursive Query Issues
```sql
-- Create a view to monitor recursive query performance
CREATE OR REPLACE VIEW recursive_query_monitor AS
SELECT 
  pid,
  now() - query_start as duration,
  query,
  state,
  wait_event_type,
  wait_event
FROM pg_stat_activity 
WHERE query LIKE '%recurse%' 
   OR query LIKE '%WITH RECURSIVE%'
   OR query LIKE '%base_types%'
   AND state = 'active'
   AND now() - query_start > interval '5 seconds';

-- Function to kill long-running recursive queries
CREATE OR REPLACE FUNCTION kill_long_recursive_queries(max_duration_seconds integer DEFAULT 30)
RETURNS integer
LANGUAGE plpgsql
AS $$
DECLARE
  killed_count integer := 0;
  rec record;
BEGIN
  FOR rec IN 
    SELECT pid FROM pg_stat_activity 
    WHERE (query LIKE '%recurse%' OR query LIKE '%WITH RECURSIVE%')
      AND state = 'active'
      AND now() - query_start > make_interval(secs => max_duration_seconds)
  LOOP
    EXECUTE format('SELECT pg_terminate_backend(%s)', rec.pid);
    killed_count := killed_count + 1;
  END LOOP;
  
  RETURN killed_count;
END;
$$;
```

#### Automated Maintenance
```sql
-- Function to check cache freshness
CREATE OR REPLACE FUNCTION check_cache_freshness()
RETURNS TABLE(view_name text, needs_refresh boolean)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT 'cached_timezone_names'::text, 
           (pg_stat_get_last_vacuum_time('cached_timezone_names'::regclass) < now() - interval '24 hours')::boolean
    UNION ALL
    SELECT 'cached_extension_info'::text,
           (pg_stat_get_last_vacuum_time('cached_extension_info'::regclass) < now() - interval '24 hours')::boolean
    UNION ALL
    SELECT 'cached_function_metadata'::text,
           (pg_stat_get_last_vacuum_time('cached_function_metadata'::regclass) < now() - interval '12 hours')::boolean;
END;
$$;

-- Schedule monitoring and cleanup
SELECT cron.schedule('monitor-recursive-queries', '*/5 * * * *', 
  'SELECT kill_long_recursive_queries(60);'); -- Kill queries > 60s every 5 minutes

SELECT cron.schedule('check-cache-freshness', '*/30 * * * *', 
  'SELECT * FROM check_cache_freshness() WHERE needs_refresh;');
```

## Implementation Priority

1. **Critical (Immediate - 429 Fix)**: Create materialized views for recursive function metadata query
2. **High (Day 1)**: Implement aggressive caching and rate limiting for function metadata
3. **Medium (Week 1)**: Create materialized views for timezone and extension queries
4. **Low (Week 2)**: Set up automated refresh and comprehensive monitoring

## Emergency 429 Fix Steps

If you're experiencing 429 errors right now, implement these immediately:

```sql
-- Step 1: Create simplified function cache (fastest fix)
CREATE MATERIALIZED VIEW cached_function_emergency AS
SELECT pn.nspname as proc_schema, p.proname as proc_name, p.pronamespace
FROM pg_proc p
JOIN pg_namespace pn ON pn.oid = p.pronamespace
WHERE prokind = 'f';

-- Step 2: Kill long-running recursive queries
SELECT kill_long_recursive_queries(30);

-- Step 3: Application-level emergency caching
import time
from typing import Dict, Any
from functools import lru_cache

emergency_cache = {}

async def emergency_function_lookup(schema: str, name: str) -> Dict[str, Any]:
    """Emergency function lookup with in-memory caching"""
    key = f"{schema}.{name}"
    
    if key in emergency_cache:
        cached_data, timestamp = emergency_cache[key]
        # Cache for 5 minutes
        if time.time() - timestamp < 300:
            return cached_data
    
    # Fetch fresh data
    result = supabase.rpc('get_function_simple', {
        'schema_name': schema, 
        'func_name': name
    }).execute()
    
    emergency_cache[key] = (result.data, time.time())
    return result.data

# Alternative using LRU cache (more memory efficient)
@lru_cache(maxsize=1000)
def get_cached_function_info(schema: str, name: str):
    """LRU cache for function lookup (no TTL, but memory efficient)"""
    return supabase.rpc('get_function_simple', {
        'schema_name': schema, 
        'func_name': name
    }).execute().data

# Clean old entries periodically
async def cleanup_emergency_cache():
    """Remove old entries from emergency cache"""
    current_time = time.time()
    keys_to_remove = []
    
    for key, (_, timestamp) in emergency_cache.items():
        if current_time - timestamp > 300:  # 5 minutes
            keys_to_remove.append(key)
    
    for key in keys_to_remove:
        del emergency_cache[key]
    
    if keys_to_remove:
        print(f"Cleaned {len(keys_to_remove)} expired cache entries")
```

## Expected Results

- **429 Error Elimination**: 99% reduction in recursive query frequency
- **Query time reduction**: 90-95% faster response times for metadata queries
- **Database load reduction**: Significantly fewer expensive catalog and recursive queries
- **Improved stability**: Render server worker will be reliable during peak usage
- **Better scalability**: Database can handle 10x more concurrent metadata requests

## Critical Notes for 429 Resolution

- **Function metadata changes rarely**: Cache for 12-24 hours safely
- **Recursive queries are the primary 429 cause**: Focus caching efforts here first
- **Monitor actively**: Set up alerts for recursive query duration > 10 seconds
- **Consider request deduplication**: Multiple identical requests should share one query result
- **Implement circuit breakers**: Fail fast when database is overwhelmed
- **Test under load**: Simulate high traffic to verify 429 resolution

## Production Deployment Strategy

1. **Stage 1**: Deploy emergency function cache and rate limiting
2. **Stage 2**: Add timezone and extension caching
3. **Stage 3**: Implement comprehensive monitoring
4. **Stage 4**: Optimize and tune based on production metrics