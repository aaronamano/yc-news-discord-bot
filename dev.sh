#!/bin/bash
# Development script for YC News Discord Bot

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Check if virtual environment exists and activate it
if [ ! -d "venv" ]; then
    print_error "Virtual environment not found. Run './setup.sh' first."
    exit 1
fi

source venv/bin/activate

# Function to check for syntax errors and warnings
check_syntax() {
    print_status "Checking Python syntax and import errors..."
    
    if python3 -m py_compile bot.py; then
        print_success "Syntax check passed"
    else
        print_error "Syntax errors found"
        return 1
    fi
}

# Function to run with error detection
check_runtime() {
    print_status "Checking for runtime warnings and errors..."
    
    # Run with tracemalloc and warnings as errors
    python3 -c "
import tracemalloc
import warnings
import sys
import os

# Enable tracemalloc to get allocation tracebacks
tracemalloc.start()

# Convert warnings to errors, but ignore external library deprecation warnings
warnings.filterwarnings('error', category=RuntimeWarning, module='bot')
warnings.filterwarnings('ignore', category=DeprecationWarning, module='.*')
warnings.filterwarnings('ignore', message='.*deprecated.*')

# Set up environment
os.environ.setdefault('PYTHONPATH', os.getcwd())

try:
    print('Importing bot module...')
    import bot
    print('✅ Module imported successfully')
    
    # Test critical functions if they exist
    if hasattr(bot, 'load_subscriptions'):
        print('✅ load_subscriptions function found')
    
    if hasattr(bot, 'run_bot_with_retry'):
        print('✅ run_bot_with_retry function found')
    
    print('✅ All runtime checks passed')
    
except Warning as e:
    print(f'❌ RuntimeWarning: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
    
except Exception as e:
    print(f'❌ Error: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
"
}

# Function to check imports
check_imports() {
    print_status "Checking imports..."
    
    python3 -c "
import sys
try:
    import discord
    import requests
    import bs4
    from supabase import create_client
    from dotenv import load_dotenv
    print('✅ All required packages imported successfully')
except ImportError as e:
    print(f'❌ Import error: {e}')
    sys.exit(1)
"
}

# Function to run the bot in development mode
run_dev() {
    print_status "Starting bot in development mode..."
    
    # Check if .env file exists
    if [ ! -f ".env" ]; then
        print_error ".env file not found. Run './setup.sh' first."
        exit 1
    fi
    
    # Run with tracemalloc and detailed logging
    python3 -c "
import tracemalloc
import asyncio
import os

# Start tracemalloc for memory debugging
tracemalloc.start()

# Set development environment
os.environ['DEBUG'] = 'true'

print('🚀 Starting YC News Discord Bot in development mode...')
print('📊 Memory tracing enabled')
print('🔍 Detailed logging enabled')
print('')

# Import and run the bot
try:
    import bot
    asyncio.run(bot.run_bot_with_retry())
except KeyboardInterrupt:
    print('')
    print('🛑 Bot stopped by user')
except Exception as e:
    print(f'❌ Bot crashed: {e}')
    import traceback
    traceback.print_exc()
"
}

# Function to show help
show_help() {
    echo "YC News Discord Bot - Development Script"
    echo ""
    echo "Usage: $0 [COMMAND]"
    echo ""
    echo "Commands:"
    echo "  check     Run all checks (syntax, imports, runtime)"
    echo "  syntax    Check Python syntax only"
    echo "  imports   Check package imports only"
    echo "  runtime   Check for runtime warnings only"
    echo "  run       Run bot in development mode"
    echo "  setup     Run initial setup"
    echo "  help      Show this help message"
    echo ""
    echo "Examples:"
    echo "  $0 check    # Run all checks before deployment"
    echo "  $0 run      # Start the bot in dev mode"
}

# Main command handling
case "${1:-help}" in
    "check")
        print_status "Running all development checks..."
        check_syntax
        check_imports
        check_runtime
        print_success "All checks passed! 🎉"
        ;;
    "syntax")
        check_syntax
        ;;
    "imports")
        check_imports
        ;;
    "runtime")
        check_runtime
        ;;
    "run")
        run_dev
        ;;
    "setup")
        ./setup.sh
        ;;
    "help"|*)
        show_help
        ;;
esac