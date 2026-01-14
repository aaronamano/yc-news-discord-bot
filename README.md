# Description
Discord bot that gets top 15 most recent news from the base url, https://hn.algolia.com/?dateRange=last24h&page=0&prefix=true&sort=byDate&type=story. It checks for this every hour.

# Get Started
1. create a `.env` file and add `DISCORD_TOKEN=<discord-token>` AND `CHANNEL_ID=<channel-id>`
2. create a virtual environment `python3 -m venv venv` and then run `source venv/bin/activate`
3. install dependencies by running `pip install -r requirements.txt`
4. run the bot with `python3 bot.py`
5. leave environemnt by running `deactivate`

# How it works
- users can subscribe to the bot to get web-scraped, personalized news on their direct messages by typing `!yc-news subscribe`
- users can opt out from the bot by typing `yc-news unsubscribe`
- to get curated feeds, they can add tags by typing `!yc-news add="AI, ML, LLMs"` to the bot on the direct message
    - as a result each word is parsed and separated by a comma so `&query=AI`, `&query=ML`, and `&query=LLMs` are added to the base url.
- if you want to remove tags, type for example `!yc-news remove="AI, SWE"` to the bot on the direct message
    - as a result each word is parsed and separated by a comma so `&query=AI` and `&query=SWE` are deleted from the base url.

# Directions

## Bot Commands (in specified channel only)
- **Subscribe**: `!yc-news subscribe` - Receive hourly news updates via DM
- **Unsubscribe**: `!yc-news unsubscribe` - Stop receiving news updates
- **Add Tags**: `!yc-news add="tag1, tag2, tag3"` - Add comma-separated tags to personalize your feed
- **Remove Tags**: `!yc-news remove="tag1, tag2"` - Remove specific tags from your feed

## File Structure
- `bot.py` - Main bot file with all functionality
- `subscriptions.json` - Stores user subscription data and tags (auto-created)
- `.env` - Environment variables (DISCORD_TOKEN, CHANNEL_ID)
- `requirements.txt` - Python dependencies

## Bot Behavior
- Users run commands in the specified channel (via CHANNEL_ID)
- Scrapes news every hour from the specified Algolia URL
- Delivers top 15 most recent stories to subscribed users via DM
- Filters content based on user's personalized tags
- Limits to 5 stories per user per hour to prevent spam
- Stores subscription data locally in JSON format

# Instructions
- make sure to use this URL to scrape `https://hn.algolia.com/?dateRange=last24h&page=0&prefix=true&sort=byDate&type=story`
- also do not use the Algolia API