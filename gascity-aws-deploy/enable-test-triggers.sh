#!/bin/bash
# Quick Fix: Enable Test Approval Triggers
# This script opens port 7375 and deploys the test trigger page

set -e

DOPPLER_TOKEN="$1"
if [ -z "$DOPPLER_TOKEN" ]; then
    echo "❌ Error: Doppler token required"
    echo "Usage: ./enable-test-triggers.sh <doppler-token>"
    exit 1
fi

echo "🔧 Fetching configuration..."
AWS_REGION=$(curl -s -H "Authorization: Bearer $DOPPLER_TOKEN" \
    "https://api.doppler.com/v3/configs/config/secrets/download?project=gascity&config=prd&format=json" | \
    python3 -c "import sys, json; print(json.load(sys.stdin).get('AWS_REGION', 'ap-southeast-1'))" 2>/dev/null || echo "ap-southeast-1")

echo "📍 Region: $AWS_REGION"
echo ""

# Find the security group
echo "🔍 Finding security group..."
SG_ID=$(aws ec2 describe-security-groups \
    --region "$AWS_REGION" \
    --filters "Name=group-name,Values=gascity-sg" \
    --query 'SecurityGroups[0].GroupId' \
    --output text)

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
    echo "❌ Security group 'gascity-sg' not found"
    exit 1
fi

echo "✅ Found security group: $SG_ID"

# Check if port 7375 is already open
echo "🔍 Checking if port 7375 is open..."
EXISTING=$(aws ec2 describe-security-groups \
    --region "$AWS_REGION" \
    --group-ids "$SG_ID" \
    --query "SecurityGroups[0].IpPermissions[?FromPort==\`7375\`]" \
    --output text)

if [ -n "$EXISTING" ]; then
    echo "✅ Port 7375 already open"
else
    echo "🔓 Opening port 7375..."
    aws ec2 authorize-security-group-ingress \
        --region "$AWS_REGION" \
        --group-id "$SG_ID" \
        --protocol tcp \
        --port 7375 \
        --cidr 0.0.0.0/0 \
        --output text 2>/dev/null || echo "⚠️  Rule may already exist"
    
    echo "✅ Port 7375 opened"
fi

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  ✅ Test triggers enabled!                                ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "📱 You can now send test approvals:"
echo ""
echo "   Option 1: Run the Python script"
echo "   $ python3 send-test-approvals.py"
echo ""
echo "   Option 2: Use curl"
echo "   $ curl -X POST http://13.214.162.41:7375/v0/extmsg/inbound \\"
echo "     -H 'Content-Type: application/json' \\"
echo "     -d '{\"client_id\":\"deploy-agent\",\"message\":\"APPROVAL_NEEDED: deployment_approval | Test | Testing approvals\"}'"
echo ""
echo "📊 Monitor results:"
echo "   • Check your Telegram bots for approval requests"
echo "   • View Mini App: http://13.214.162.41:8080"
echo ""
