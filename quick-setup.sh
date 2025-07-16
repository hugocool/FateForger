#!/bin/bash
# Quick setup script for Admonish with Poetry
# For environments like OpenAI Codex with Python 3.12+ already installed

set -e

echo "⚡ Quick Admonish setup for Python 3.12+ environments..."

# Check if poetry is installed
if ! command -v poetry &> /dev/null; then
    echo "� Installing Poetry package manager..."
    curl -sSL https://install.python-poetry.org | python3 -
    
    # Add poetry to PATH for this session
    export PATH="$HOME/.local/bin:$PATH"
    
    # Check if poetry is now available
    if ! command -v poetry &> /dev/null; then
        echo "❌ Poetry installation failed. Please install manually: https://python-poetry.org/docs/#installation"
        exit 1
    fi
fi

echo "✅ Poetry found: $(poetry --version)"

# Configure poetry for local development
echo "� Configuring Poetry..."
poetry config virtualenvs.in-project true

# Install dependencies
echo "📚 Installing dependencies with Poetry..."
poetry install

# Create data directory
mkdir -p data

# Create minimal .env file
if [ ! -f ".env" ]; then
    cat > .env << 'EOF'
# Minimal configuration for development
SLACK_BOT_TOKEN=xoxb-placeholder
SLACK_SIGNING_SECRET=placeholder
DATABASE_URL=sqlite:///data/admonish.db
DEBUG=true
EOF
    echo "✅ Created minimal .env file"
fi

# Initialize database
echo "🗄️  Setting up database..."
python -c "
import sqlite3
from pathlib import Path

# Create database file
db_path = Path('data/admonish.db')
db_path.parent.mkdir(exist_ok=True)

# Basic table creation
conn = sqlite3.connect(str(db_path))
cursor = conn.cursor()

# Create basic planning_sessions table
cursor.execute('''
CREATE TABLE IF NOT EXISTS planning_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    date DATE NOT NULL,
    scheduled_for TIMESTAMP,
    status TEXT DEFAULT 'NOT_STARTED',
    goals TEXT,
    plan TEXT,
    slack_scheduled_message_id TEXT,
    haunt_attempt INTEGER DEFAULT 0,
    scheduler_job_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
''')

conn.commit()
conn.close()
print('✅ Basic database initialized')
"

# Test the setup
echo "🧪 Testing setup..."
python -c "
import sys
print(f'✅ Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')

try:
    import sqlalchemy
    import slack_bolt
    import pytest
    print('✅ Core dependencies imported successfully')
except ImportError as e:
    print(f'❌ Import error: {e}')
    sys.exit(1)

try:
    import sqlite3
    conn = sqlite3.connect('data/admonish.db')
    cursor = conn.cursor()
    cursor.execute('SELECT name FROM sqlite_master WHERE type=\"table\";')
    tables = cursor.fetchall()
    conn.close()
    print(f'✅ Database connection successful ({len(tables)} tables)')
except Exception as e:
    print(f'❌ Database error: {e}')
"

echo ""
echo "🎉 Quick setup complete!"
echo ""
echo "🚀 To start developing:"
echo "  source venv/bin/activate       # Activate environment"
echo "  python -m pytest tests/       # Run tests"
echo "  python -c 'import src.productivity_bot.common'  # Test imports"
echo ""
echo "📝 Next steps:"
echo "  1. Update .env with real Slack tokens"
echo "  2. Run: source venv/bin/activate"
echo "  3. Test: python -c 'from src.productivity_bot.haunter_bot import haunt_user'"
echo ""
