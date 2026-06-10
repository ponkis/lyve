#!/bin/bash
# Exit on any error
set -e

PROJECT_DIR="/home/ponkis/lyve"
PM2_APP_NAME="lyve-backend"

echo "📂 navigating to project directory..."
cd "$PROJECT_DIR"

echo "📦 pulling latest code from git..."
git checkout main
git fetch origin
git pull origin main

# Check if origin/dev remote branch exists before attempting to merge
if git show-ref --verify --quiet refs/remotes/origin/dev; then
    echo "🔄 merging origin/dev..."
    git merge origin/dev
    git push origin main
else
    echo "ℹ️ origin/dev branch does not exist on remote yet, skipping merge."
fi

echo "🐍 updating python environment dependencies..."
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install gunicorn

echo "🚀 restarting app under PM2..."
# If the app is already running under PM2, restart it. Otherwise, start it using ecosystem config.
if pm2 show "$PM2_APP_NAME" > /dev/null 2>&1; then
    pm2 restart "$PM2_APP_NAME"
    pm2 flush "$PM2_APP_NAME"
else
    pm2 start ecosystem.config.js
fi

echo "✅ deployment completed successfully!"
pm2 logs "$PM2_APP_NAME" --lines 30 --no-daemon
