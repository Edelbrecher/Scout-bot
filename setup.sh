#!/bin/bash
set -e

echo "=== TD Scouter Setup ==="

# Check Docker
if ! command -v docker &>/dev/null; then
  echo "Installing Docker..."
  curl -fsSL https://get.docker.com | sh
  systemctl enable docker
  systemctl start docker
fi

# Check Docker Compose
if ! docker compose version &>/dev/null; then
  echo "Installing Docker Compose plugin..."
  apt-get update && apt-get install -y docker-compose-plugin
fi

# Create .env if missing
if [ ! -f .env ]; then
  cp .env.example .env
  echo ""
  echo ">>> .env file created. Please edit it now:"
  echo "    nano .env"
  echo ""
  echo "Set DISCORD_TOKEN, SECRET_KEY, ADMIN_USERNAME, ADMIN_PASSWORD"
  echo "Then re-run: ./setup.sh"
  exit 0
fi

# Ensure data dir exists
mkdir -p data

echo "Building and starting containers..."
docker compose up -d --build

echo ""
echo "=== Done! ==="
echo "Web admin: http://$(curl -s ifconfig.me 2>/dev/null || echo '<your-server-ip>'):8080"
echo ""
echo "Bot is running. Invite it to your Discord server, then:"
echo "  1. Open the web admin and configure Category ID + Archive Channel ID"
echo "  2. In Discord, run /setup-scout in the channel for the button"
