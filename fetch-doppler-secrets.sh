#!/bin/bash
# Fetch secrets from Doppler for local testing

DOPPLER_TOKEN="${1}"

if [ -z "$DOPPLER_TOKEN" ]; then
    echo "❌ Usage: $0 <doppler-service-token>"
    echo "   Get token from: doppler configs tokens create"
    exit 1
fi

echo "🔐 Fetching secrets from Doppler..."

curl -s --request GET \
  --url https://api.doppler.com/v3/configs/config/secrets/download \
  --header "Authorization: Bearer ${DOPPLER_TOKEN}" \
  --header 'Accept: application/json' > /tmp/doppler-secrets.json

if [ $? -eq 0 ] && [ -s /tmp/doppler-secrets.json ]; then
    echo "✅ Secrets fetched successfully!"
    
    # Extract the secrets we need
    export TELEGRAM_BOT_TOKEN=$(cat /tmp/doppler-secrets.json | grep -o '"TELEGRAM_BOT_TOKEN":"[^"]*"' | cut -d'"' -f4)
    export TELEGRAM_BOT_TOKEN_NORDICE=$(cat /tmp/doppler-secrets.json | grep -o '"TELEGRAM_BOT_TOKEN_NORDICE":"[^"]*"' | cut -d'"' -f4)
    export TELEGRAM_USER_ID=$(cat /tmp/doppler-secrets.json | grep -o '"TELEGRAM_USER_ID":"[^"]*"' | cut -d'"' -f4)
    
    echo ""
    echo "📋 Retrieved secrets:"
    echo "  TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:0:20}..."
    echo "  TELEGRAM_BOT_TOKEN_NORDICE: ${TELEGRAM_BOT_TOKEN_NORDICE:0:20}..."
    echo "  TELEGRAM_USER_ID: ${TELEGRAM_USER_ID}"
    
    # Save to env file
    cat > /tmp/test-bot.env << ENVEOF
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_BOT_TOKEN_NORDICE=${TELEGRAM_BOT_TOKEN_NORDICE}
TELEGRAM_USER_ID=${TELEGRAM_USER_ID}
ENVEOF
    
    echo ""
    echo "✅ Environment variables saved to /tmp/test-bot.env"
    rm -f /tmp/doppler-secrets.json
else
    echo "❌ Failed to fetch secrets from Doppler"
    exit 1
fi
