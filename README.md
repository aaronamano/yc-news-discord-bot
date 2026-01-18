# YC News Discord Bot

A Discord bot that fetches and delivers personalized Hacker News updates every hour based on user preferences and tags.

## Quick Start

1. **Create environment file**
   ```bash
   cp .env.example .env
   # Edit .env and add your DISCORD_TOKEN and CHANNEL_ID
   ```

2. **Set up virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the bot**
   ```bash
   python3 bot.py
   ```

5. **Deactivate when done**
   ```bash
   deactivate
   ```

## Features

- **Personalized News**: Users can subscribe and receive curated Hacker News stories based on their interests
- **Tag-Based Filtering**: Add/remove tags to customize the news feed (AI, ML, startups, etc.)
- **Hourly Updates**: Automatically fetches and delivers the top 15 most recent stories every hour
- **DM Delivery**: News is sent directly to users via Discord direct messages
- **Rate Limiting**: Limits to 5 stories per user per hour to prevent spam

## How It Works

The bot scrapes Hacker News via Algolia's API every hour and delivers personalized content to subscribed users:

1. **Subscription Management**: Users subscribe/unsubscribe via bot commands
2. **Tag Customization**: Users can add/remove tags to personalize their feed
3. **Content Filtering**: Tags are added as query parameters to filter stories
4. **Automated Delivery**: Top stories are delivered via DM every hour

## Usage

### Bot Commands

Run these commands in the specified Discord channel (configured via `CHANNEL_ID`):

| Command | Description |
|---------|-------------|
| `!yc-news subscribe` | Subscribe to hourly news updates via DM |
| `!yc-news unsubscribe` | Unsubscribe from news updates |
| `!yc-news add="tag1, tag2"` | Add comma-separated tags to personalize feed |
| `!yc-news remove="tag1, tag2"` | Remove specific tags from your feed |
| `!yc-news tags` | View all your current tags |

**Examples:**
```bash
!yc-news add="AI, ML, LLMs"
!yc-news remove="AI, SWE"
```

## Project Structure

```
yc-news-discord-bot/
├── bot.py                 # Main bot implementation
├── subscriptions.db       # SQLite database for user subscriptions and tags (auto-created)
├── .env                   # Environment variables (DISCORD_TOKEN, CHANNEL_ID)
├── .env.example           # Example environment file
├── requirements.txt       # Python dependencies
└── README.md             # This file
```

## Technical Details

- **Data Source**: [Hacker News via Algolia API](https://hn.algolia.com/?dateRange=last24h&page=0&prefix=true&sort=byDate&type=story)
- **Scraping Frequency**: Every hour
- **Story Limit**: Top 15 most recent stories, 5 delivered per user
- **Storage**: SQLite database for subscriptions and user preferences
- **Rate Limiting**: Built-in spam protection (5 stories/user/hour)

## Database Schema

```sql
CREATE TABLE subscriptions (
    userId TEXT PRIMARY KEY,
    subscribed BOOLEAN DEFAULT FALSE,
    tags TEXT DEFAULT '[]'
)
```