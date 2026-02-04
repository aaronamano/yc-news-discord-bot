# YC News Discord Bot - Development Guide

## Development Environment Setup

### Quick Start

1. **Initial Setup**
   ```bash
   ./setup.sh
   ```
   This will:
   - Create a Python virtual environment
   - Install all dependencies
   - Create a `.env` file template

2. **Configure Environment**
   Edit the `.env` file with your actual configuration values:
   - `DISCORD_BOT_TOKEN`: Your Discord bot token
   - `DISCORD_CLIENT_ID`: Your Discord application ID
   - `SUPABASE_URL`: Your Supabase project URL
   - `SUPABASE_ANON_KEY`: Your Supabase anonymous key

3. **Run Checks Before Deployment**
   ```bash
   ./dev.sh check
   ```
   This runs comprehensive checks to catch errors before deployment:
   - Python syntax validation
   - Import verification
   - Runtime warning detection

## Development Commands

### Available Commands

```bash
# Run all checks (recommended before deployment)
./dev.sh check

# Check specific components
./dev.sh syntax     # Python syntax only
./dev.sh imports    # Package imports only
./dev.sh runtime    # Runtime warnings only

# Run bot in development mode
./dev.sh run

# Show help
./dev.sh help

# Re-run initial setup
./dev.sh setup
```

### Development Mode Features

When running `./dev.sh run`, the bot includes:
- ✅ **Memory tracing** with `tracemalloc` for debugging memory leaks
- ✅ **Enhanced logging** with detailed debug information
- ✅ **Runtime error detection** with immediate feedback
- ✅ **Keyboard interrupt handling** for graceful shutdown

## Error Prevention

### Pre-deployment Checklist

Before deploying to production, always run:

1. **Full Health Check**
   ```bash
   ./dev.sh check
   ```
   - Catches syntax errors
   - Verifies all imports work
   - Detects runtime warnings (like unawaited coroutines)
   - Validates critical functions exist

2. **Manual Testing**
   - Test bot commands in a Discord test server
   - Verify database connections
   - Check Redis connectivity (if used)

### Common Issues Fixed

- **Unawaited Coroutines**: The development setup detects RuntimeWarnings like the one you experienced
- **Missing Dependencies**: Import check ensures all packages are available
- **Syntax Errors**: Compilation check catches issues before runtime

## File Structure

```
yc-news-discord-bot/
├── bot.py              # Main bot implementation
├── requirements.txt    # Python dependencies
├── .env               # Environment variables (create from template)
├── setup.sh           # Initial environment setup
├── dev.sh             # Development commands and checks
├── run_dev.py         # Development runner with tracing
└── venv/              # Python virtual environment
```

## Deployment Workflow

1. **Make Changes** - Edit your code
2. **Run Checks** - `./dev.sh check` to verify everything works
3. **Fix Issues** - Address any problems found
4. **Deploy** - Push to production (only after checks pass)

This workflow ensures you catch issues like the coroutine warning before they reach production!