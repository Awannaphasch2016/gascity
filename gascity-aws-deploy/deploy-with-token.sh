#!/bin/bash
# Doppler-enabled deployment that YOU can paste a service token into
set -e

echo "🔐 Doppler Service Token Setup"
echo "=============================="
echo ""

# Check if DOPPLER_TOKEN is already set
if [ -z "$DOPPLER_TOKEN" ]; then
    echo "📝 To get your Doppler service token:"
    echo "   1. Go to: https://dashboard.doppler.com"
    echo "   2. Select project: gascity"
    echo "   3. Select config: prd"
    echo "   4. Go to: Access > Service Tokens"
    echo "   5. Click: Generate"
    echo "   6. Copy the token (starts with 'dp.st.')"
    echo ""
    read -sp "Paste your Doppler service token: " DOPPLER_TOKEN
    echo ""
    export DOPPLER_TOKEN
fi

echo "✅ Doppler token set"

# Fetch secrets using token
echo "📥 Fetching secrets from Doppler..."

# Use Doppler API to fetch secrets
SECRETS=$(curl -s "https://api.doppler.com/v3/configs/config/secrets/download?format=json" \
  -H "Authorization: Bearer ${DOPPLER_TOKEN}")

# Extract values
export AWS_ACCESS_KEY_ID=$(echo "$SECRETS" | jq -r '.AWS_ACCESS_KEY_ID')
export AWS_SECRET_ACCESS_KEY=$(echo "$SECRETS" | jq -r '.AWS_SECRET_ACCESS_KEY')
export AWS_REGION=$(echo "$SECRETS" | jq -r '.AWS_REGION')
export TELEGRAM_BOT_TOKEN=$(echo "$SECRETS" | jq -r '.TELEGRAM_BOT_TOKEN')
export INSTANCE_TYPE=$(echo "$SECRETS" | jq -r '.INSTANCE_TYPE // "t3.medium"')

echo "✅ Secrets loaded from Doppler"

# Run the deployment
echo ""
echo "🚀 Starting AWS deployment..."
./deploy.sh
