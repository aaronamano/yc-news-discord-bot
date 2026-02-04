# YC News Bot Deployment Guide (Optimized for 429 Error Prevention)

## 🚀 Overview

Your YC News bot has been optimized to prevent 429 errors by:
1. **Consolidated Architecture** - Single Discord bot application
2. **Eliminated API Server** - Removed expensive database operations
3. **Optimized Database Calls** - Reduced frequency and load

This guide covers deploying the optimized bot on Render.

---

## 📁 Project Structure

```
yc-news-discord-bot/
├── bot.py              # Discord bot (consolidated functionality)
├── requirements.txt     # Dependencies
├── .env               # Environment variables
└── start.sh           # Simplified startup script
```

---

## 🔧 Environment Variables

Add these to your Render environment variables:

### Required for Bot:
```bash
# Discord Configuration
DISCORD_TOKEN=your_discord_bot_token
CHANNEL_ID=your_discord_channel_id

# Database Configuration  
SUPABASE_URL=your_supabase_url
SUPABASE_KEY=your_supabase_key
```

### Security Notes:
- Never commit `.env` to version control
- Use Render's environment variable management
- Ensure Discord bot has proper permissions in your server

---

## 📦 Dependencies

Your `requirements.txt` includes:
```
discord.py
requests
beautifulsoup4
python-dotenv
supabase
```

---

## 🏗️ Render Deployment

**Using the simplified `start.sh`:**
```bash
#!/bin/bash
# Start Discord bot
python bot.py
```

**Make executable:**
```bash
chmod +x start.sh
```

**Render Service Settings:**
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `./start.sh`

---

## 🎯 Bot Commands Available

| Command | Description | Example |
|---------|-------------|---------|
| `!yc-news subscribe` | Subscribe to news updates | `!yc-news subscribe` |
| `!yc-news unsubscribe` | Unsubscribe from updates | `!yc-news unsubscribe` |
| `!yc-news add="tags"` | Add keywords for matching | `!yc-news add="AI, ML"` |
| `!yc-news remove="tags"` | Remove keywords | `!yc-news remove="AI, ML"` |
| `!yc-news tags` | View current keywords | `!yc-news tags` |

---

## 🛠️ Optimizations Applied

### ✅ **429 Error Prevention**
- **Reduced News Frequency**: Changed from hourly to every 6 hours
- **Limited Database Calls**: Process only 5 users and 20 stories per cycle
- **In-Memory Caching**: 5-minute cache to reduce repeated database queries
- **Longer Delays**: Increased delays between DMs and operations

### ✅ **Database Load Reduction**
- **Simplified Matching**: Direct keyword matching without heavy recursive queries
- **Batch Processing**: Limited user and story processing per cycle
- **Consolidated Code**: Single file eliminates service-to-service communication

---

## 📊 Monitoring & Logging

### Checking Bot Status
- Check Render dashboard for application logs
- Bot outputs connection status and errors to console
- Look for "[INFO]" messages for successful operations

### Key Log Messages
- `[INFO] Bot is ready! Logged in as YourBotName`
- `[INFO] Rate limited. Waiting Xs before retry...`
- `[ERROR] Error in send_news_dms: error_message`

---

## 🚨 Troubleshooting

### Common Issues:

1. **Bot Not Responding to Commands**
   - Ensure bot has proper permissions in Discord
   - Check `CHANNEL_ID` is correct
   - Verify bot is online in Discord server
   - Check Render logs for connection errors

2. **429 Errors Still Occurring**
   - Should be eliminated with optimizations
   - Check if `send_news_dms` is running too frequently
   - Monitor database query patterns in logs

3. **Database Connection Issues**
   - Verify `SUPABASE_URL` and `SUPABASE_KEY` are correct
   - Check Supabase service status
   - Ensure subscription table exists

4. **Bot Keeps Restarting**
   - Check memory usage on Render dashboard
   - Review error logs for crash causes
   - Ensure all environment variables are set

---

## 🔒 Security Considerations

1. **Discord Token Protection**
   - Keep `DISCORD_TOKEN` secure in environment variables
   - Rotate tokens periodically for security
   - Monitor bot's permissions in Discord

2. **Database Security**
   - Use read-only database keys if possible
   - Implement proper user data isolation
   - Regular backups of subscription data

---

## 📈 Performance Monitoring

### Key Metrics to Watch:
- **Bot Uptime**: Should be stable after optimization
- **Database Queries**: Significantly reduced (goal: < 10 per hour)
- **Memory Usage**: Should be stable with in-memory caching
- **Error Rate**: 429 errors should be eliminated

### Render Dashboard Checks:
- **Response Time**: Bot should respond quickly to commands
- **Memory Usage**: Monitor for memory leaks
- **CPU Usage**: Should be low with reduced processing

---

## 🔄 Maintenance

### Regular Tasks:
1. **Monitor Logs**: Check for any recurring errors
2. **Update Dependencies**: Keep packages updated
3. **Backup Data**: Export subscription data periodically
4. **Performance Review**: Check optimization effectiveness

### Upgrade Process:
1. Test changes in development environment
2. Deploy to Render using automatic builds
3. Monitor post-deployment performance
4. Rollback if issues detected

---

Your optimized YC News bot is now ready for deployment! 🚀

The consolidation approach eliminates the root cause of 429 errors while maintaining all Discord functionality.