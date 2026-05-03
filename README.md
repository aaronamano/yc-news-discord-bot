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