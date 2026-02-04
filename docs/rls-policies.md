# Row Level Security (RLS) Policies

This document contains RLS policies for user subscription data access control in Supabase.

## Database Schema
```sql
CREATE TABLE subscriptions (
    userId TEXT PRIMARY KEY,
    subscribed BOOLEAN DEFAULT FALSE,
    tags TEXT DEFAULT '[]'
);
```

## Updated RLS Policies for Bot Access

### 1. Service key can manage all subscriptions (for bot operations)
```sql
-- Drop existing policies first
drop policy if exists "Users can insert valid own subscription" on "public"."subscriptions";
drop policy if exists "Users can update own subscription and tags" on "public"."subscriptions";
drop policy if exists "Users can view own subscription" on "public"."subscriptions";

-- Allow service role (bot) to manage all subscriptions
create policy "Service role can manage all subscriptions"
on "public"."subscriptions"
to service_role
using (true)
with check (true);
```

### 2. Authenticated users can manage their own subscriptions (if needed for web interface)
```sql
create policy "Users can manage own subscription"
on "public"."subscriptions"
to authenticated
using (
  ((auth.uid())::text = "userId")
) with check (
  ((auth.uid())::text = "userId") AND 
  (subscribed IS NOT NULL) AND 
  ("userId" IS NOT NULL)
);
```

### 3. Public read access (optional - remove if not needed)
```sql
-- Allow anonymous users to view subscriptions (optional)
create policy "Allow public read access"
on "public"."subscriptions"
to public
for select
using (false); -- Set to true if you want public read access
```

### Complete SQL to Apply All Policies
```sql
-- Enable RLS on the table
alter table "public"."subscriptions" enable row level security;

-- Drop any existing policies
drop policy if exists "Users can insert valid own subscription" on "public"."subscriptions";
drop policy if exists "Users can update own subscription and tags" on "public"."subscriptions";
drop policy if exists "Users can view own subscription" on "public"."subscriptions";

-- Service role policy for bot (allows all operations)
create policy "Service role can manage all subscriptions"
on "public"."subscriptions"
to service_role
using (true)
with check (true);

-- Authenticated user policy (for potential web interface)
create policy "Users can manage own subscription"
on "public"."subscriptions"
to authenticated
using (
  ((auth.uid())::text = "userId")
) with check (
  ((auth.uid())::text = "userId") AND 
  (subscribed IS NOT NULL) AND 
  ("userId" IS NOT NULL)
);
```

### 4. Alternative: Disable RLS for Service Role (Simpler)
If you want to completely bypass RLS for the service role (simpler approach):

```sql
-- Enable RLS
alter table "public"."subscriptions" enable row level security;

-- Drop existing policies
drop policy if exists "Users can insert valid own subscription" on "public"."subscriptions";
drop policy if exists "Users can update own subscription and tags" on "public"."subscriptions";
drop policy if exists "Users can view own subscription" on "public"."subscriptions";

-- Create simple policy allowing service role full access
create policy "Allow service role full access"
on "public"."subscriptions"
to service_role
using (true)
with check (true);
```

### Why This Fix Works:

1. **Service Role Access**: The bot uses Supabase service key, which has `service_role` privileges
2. **Previous Issue**: Old policies were designed for `auth.uid()` (authenticated users) 
3. **New Solution**: Service role policy allows bot to bypass user-based authentication
4. **Future-Proof**: Still maintains user authentication for potential web interface

### How to Apply in Supabase:

1. Go to your Supabase project
2. Navigate to **Authentication** → **Policies**
3. Select the `subscriptions` table
4. **Delete** all existing policies
5. **Create new policy** and paste one of the SQL blocks above
6. Or use **SQL Editor** to run the complete SQL script

### Test the Fix:

After applying the policies, test with:
```bash
# In Discord
!yc-news subscribe
```

The bot should now be able to create and manage subscriptions without RLS violations.