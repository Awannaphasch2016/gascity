#!/bin/bash
# AWS Deployment using Doppler API directly
set -e

echo "🚀 Gas City AWS Deployment"
echo "=========================================="
echo ""

# Doppler API token from environment or argument
DOPPLER_TOKEN="${DOPPLER_TOKEN:-${1}}"

if [ -z "$DOPPLER_TOKEN" ]; then
    echo "❌ No Doppler token provided"
    echo "   Usage: DOPPLER_TOKEN=<token> ./deploy-now.sh"
    echo "   Or: ./deploy-now.sh <token>"
    exit 1
fi

echo "🔐 Fetching secrets from Doppler API..."
SECRETS_JSON=$(curl -s --request GET \
  --url 'https://api.doppler.com/v3/configs/config/secrets/download' \
  --header "Authorization: Bearer ${DOPPLER_TOKEN}" \
  --header 'Accept: application/json')

# Check if fetch was successful
if echo "$SECRETS_JSON" | grep -q "Invalid Auth token"; then
    echo "❌ Doppler token is invalid or expired"
    echo "   Please provide a fresh token"
    exit 1
fi

echo "✅ Secrets fetched successfully"

# Extract secrets
export AWS_ACCESS_KEY_ID=$(echo "$SECRETS_JSON" | grep -o '"AWS_ACCESS_KEY_ID":"[^"]*"' | cut -d'"' -f4)
export AWS_SECRET_ACCESS_KEY=$(echo "$SECRETS_JSON" | grep -o '"AWS_SECRET_ACCESS_KEY":"[^"]*"' | cut -d'"' -f4)
export AWS_REGION=$(echo "$SECRETS_JSON" | grep -o '"AWS_REGION":"[^"]*"' | cut -d'"' -f4)
export TELEGRAM_BOT_TOKEN=$(echo "$SECRETS_JSON" | grep -o '"TELEGRAM_BOT_TOKEN":"[^"]*"' | cut -d'"' -f4)
export TELEGRAM_BOT_TOKEN_NORDICE=$(echo "$SECRETS_JSON" | grep -o '"TELEGRAM_BOT_TOKEN_NORDICE":"[^"]*"' | cut -d'"' -f4)
export TELEGRAM_USER_ID=$(echo "$SECRETS_JSON" | grep -o '"TELEGRAM_USER_ID":"[^"]*"' | cut -d'"' -f4)
export INSTANCE_TYPE=$(echo "$SECRETS_JSON" | grep -o '"INSTANCE_TYPE":"[^"]*"' | cut -d'"' -f4)

# Use defaults if not set
AWS_REGION="${AWS_REGION:-ap-southeast-1}"
INSTANCE_TYPE="${INSTANCE_TYPE:-t3.medium}"

# Validate required secrets
if [ -z "$AWS_ACCESS_KEY_ID" ] || [ -z "$AWS_SECRET_ACCESS_KEY" ] || [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo "❌ Missing required secrets"
    echo "   AWS_ACCESS_KEY_ID: ${AWS_ACCESS_KEY_ID:+SET}"
    echo "   AWS_SECRET_ACCESS_KEY: ${AWS_SECRET_ACCESS_KEY:+SET}"
    echo "   TELEGRAM_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:+SET}"
    exit 1
fi

echo "✅ All required secrets loaded"
echo "   Region: $AWS_REGION"
echo "   Instance: $INSTANCE_TYPE"
echo ""

# Now run the actual deployment commands from deploy.sh
# ... (I'll add the deployment logic here)

echo "🎯 Starting AWS deployment..."
echo ""

# Rest of deploy.sh logic continues...
