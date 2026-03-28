# Row Level Security (RLS) Policies

This document contains RLS policies for user subscription data access control in Supabase.

## Database Schema
```sql
CREATE TABLE subscriptions (
    userId TEXT PRIMARY KEY,
    subscribed BOOLEAN DEFAULT FALSE
);
```

## Basic RLS Policies

### Enable RLS
```sql
ALTER TABLE "public"."subscriptions" ENABLE ROW LEVEL SECURITY;
```

### 1. Service role can manage all subscriptions (for bot operations)
```sql
CREATE POLICY "Service role can manage all subscriptions"
ON "public"."subscriptions"
TO service_role
USING (true)
WITH CHECK (true);
```

### 2. Users can only access their own subscription
```sql
CREATE POLICY "Users can access own subscription"
ON "public"."subscriptions"
TO authenticated
USING ((auth.uid())::text = "userId")
WITH CHECK ((auth.uid())::text = "userId");
```

### Complete SQL to Apply All Policies
```sql
-- Enable RLS
ALTER TABLE "public"."subscriptions" ENABLE ROW LEVEL SECURITY;

-- Service role policy for bot (full access)
CREATE POLICY "Service role can manage all subscriptions"
ON "public"."subscriptions"
TO service_role
USING (true)
WITH CHECK (true);

-- User policy (only own subscription)
CREATE POLICY "Users can access own subscription"
ON "public"."subscriptions"
TO authenticated
USING ((auth.uid())::text = "userId")
WITH CHECK ((auth.uid())::text = "userId");
```

### How to Apply in Supabase

1. Go to your Supabase project
2. Navigate to **SQL Editor**
3. Run the complete SQL script above
