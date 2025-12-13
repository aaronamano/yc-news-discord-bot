## Description
Discord bot that gets top 15 most recent news from https://news.ycombinator.com/newest. It checks for this every hour.

## Get Started
1. create a `.env` file and add `DISCORD_TOKEN=<discord-token>` AND `CHANNEL_ID=<channel-id>`
2. create a virtual environment `python3 -m venv venv` and then run `source venv/bin/activate`
3. install dependencies by running `pip install -r requirements.txt`
4. run the bot with `python3 bot.py`
5. leave environemnt by running `deactivate`