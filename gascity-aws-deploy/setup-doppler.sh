#!/bin/bash
# Quick Doppler Setup Script
set -e

echo "🔐 Gas City Doppler Setup"
echo "========================="
echo ""

# Check Doppler
if ! command -v doppler &> /dev/null; then
    echo "📦 Installing Doppler CLI..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install dopplerhq/cli/doppler
    else
        curl -sLf 'https://packages.doppler.com/public/cli/install.sh' | sh
    fi
fi

echo "✅ Doppler CLI installed"

# Login
if ! doppler me &> /dev/null; then
    echo "🔑 Please login to Doppler..."
    doppler login
fi

echo "✅ Logged into Doppler"

# Create project
echo ""
echo "📂 Setting up project..."
doppler projects create gascity 2>/dev/null || echo "Project already exists"
doppler setup --project gascity --config prd

echo "✅ Project configured"

# Prompt for secrets
echo ""
echo "🔐 Let's add your secrets..."
echo ""

read -p "AWS Access Key ID: " AWS_KEY
doppler secrets set AWS_ACCESS_KEY_ID="$AWS_KEY"

read -sp "AWS Secret Access Key: " AWS_SECRET
echo ""
doppler secrets set AWS_SECRET_ACCESS_KEY="$AWS_SECRET"

read -p "AWS Region (default: us-east-1): " AWS_REGION
AWS_REGION=${AWS_REGION:-us-east-1}
doppler secrets set AWS_REGION="$AWS_REGION"

read -p "Telegram Bot Token: " TELEGRAM_TOKEN
doppler secrets set TELEGRAM_BOT_TOKEN="$TELEGRAM_TOKEN"

read -p "Instance Type (default: t3.medium): " INSTANCE_TYPE
INSTANCE_TYPE=${INSTANCE_TYPE:-t3.medium}
doppler secrets set INSTANCE_TYPE="$INSTANCE_TYPE"

echo ""
echo "✅ All secrets added!"
echo ""
echo "Your secrets:"
doppler secrets
echo ""
echo "Next steps:"
echo "1. Edit config/responsibilities.json with your Telegram IDs"
echo "2. Run: doppler run -- ./deploy.sh"
echo ""
