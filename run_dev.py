#!/usr/bin/env python3
import tracemalloc
import asyncio
import sys
import os

# Start tracemalloc to get allocation tracebacks
tracemalloc.start()

# Add current directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    # Import and run the bot
    import bot
    
    if __name__ == "__main__":
        asyncio.run(bot.run_bot_with_retry())
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()