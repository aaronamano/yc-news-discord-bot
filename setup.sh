#!/bin/bash
# Development setup script for YC News Discord Bot

set -e

echo "🚀 Setting up YC News Discord Bot development environment..."

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 is required but not installed. Please install Python 3 first."
    exit 1
fi

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "📦 Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
echo "🔌 Activating virtual environment..."
source venv/bin/activate

# Install dependencies
echo "📚 Installing dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "⚙️ Creating .env file template..."
    cat > .env << EOF
# Discord Bot Configuration
DISCORD_BOT_TOKEN=your_discord_bot_token_here
DISCORD_CLIENT_ID=your_discord_client_id_here

# Supabase Configuration
SUPABASE_URL=your_supabase_url_here
SUPABASE_ANON_KEY=your_supabase_anon_key_here

# Redis Configuration (optional)
REDIS_URL=redis://localhost:6379

# Development Settings
DEBUG=true
LOG_LEVEL=INFO
EOF
    echo "✅ Created .env file. Please fill in your configuration values."
fi

echo "✅ Development environment setup complete!"
echo ""
echo "🎯 Next steps:"
echo "1. Fill in your configuration values in .env"
echo "2. Run './dev.sh check' to check for errors"
echo "3. Run './dev.sh run' to start the bot in development mode"