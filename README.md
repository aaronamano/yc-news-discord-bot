# YC News Discord Bot

A Discord bot that fetches and delivers Hacker News updates every 20 hours to subscribed users.

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

- **Automated News Delivery**: Users receive the latest Hacker News stories every 20 hours
- **DM Delivery**: News is sent directly to users via Discord direct messages
- **Story Limit**: Top 20 stories delivered per user per cycle

## How It Works

The bot scrapes Hacker News via Algolia's API every 20 hours and delivers stories to subscribed users:

1. **Subscription Management**: Users subscribe/unsubscribe via bot commands
2. **Automated Delivery**: Top 20 stories are delivered via DM every 20 hours

## Usage

### Bot Commands

Run these commands in the specified Discord channel (configured via `CHANNEL_ID`):

| Command | Description |
|---------|-------------|
| `!yc-news subscribe` | Subscribe to news updates via DM |
| `!yc-news unsubscribe` | Unsubscribe from news updates |

## Project Structure

```
yc-news-discord-bot/
├── bot.py                 # Main bot implementation
├── .env                   # Environment variables (DISCORD_TOKEN, CHANNEL_ID)
├── .env.example           # Example environment file
├── requirements.txt       # Python dependencies
└── README.md             # This file
```

## Technical Details

- **Data Source**: [Hacker News via Algolia API](https://hn.algolia.com/?dateRange=last24h&page=0&prefix=true&sort=byDate&type=story)
- **Scraping Frequency**: Every 20 hours
- **Story Limit**: Top 20 stories delivered per user
- **Storage**: Supabase database for subscriptions
- **Rate Limiting**: Built-in spam protection (20 stories/user/cycle)

## Database Schema

```sql
CREATE TABLE subscriptions (
    userId TEXT PRIMARY KEY,
    subscribed BOOLEAN DEFAULT FALSE
)
```