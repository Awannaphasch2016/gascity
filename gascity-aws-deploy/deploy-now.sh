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

# Extract secrets using jq if available, otherwise use grep
if command -v jq &> /dev/null; then
    export AWS_ACCESS_KEY_ID=$(echo "$SECRETS_JSON" | jq -r '.AWS_ACCESS_KEY_ID // empty')
    export AWS_SECRET_ACCESS_KEY=$(echo "$SECRETS_JSON" | jq -r '.AWS_SECRET_ACCESS_KEY // empty')
    export AWS_REGION=$(echo "$SECRETS_JSON" | jq -r '.AWS_REGION // empty')
    export TELEGRAM_BOT_TOKEN=$(echo "$SECRETS_JSON" | jq -r '.TELEGRAM_BOT_TOKEN // empty')
    export TELEGRAM_BOT_TOKEN_NORDICE=$(echo "$SECRETS_JSON" | jq -r '.TELEGRAM_BOT_TOKEN_NORDICE // empty')
    export TELEGRAM_USER_ID=$(echo "$SECRETS_JSON" | jq -r '.TELEGRAM_USER_ID // empty')
    export INSTANCE_TYPE=$(echo "$SECRETS_JSON" | jq -r '.INSTANCE_TYPE // empty')
else
    export AWS_ACCESS_KEY_ID=$(echo "$SECRETS_JSON" | grep -o '"AWS_ACCESS_KEY_ID":"[^"]*"' | cut -d'"' -f4)
    export AWS_SECRET_ACCESS_KEY=$(echo "$SECRETS_JSON" | grep -o '"AWS_SECRET_ACCESS_KEY":"[^"]*"' | cut -d'"' -f4)
    export AWS_REGION=$(echo "$SECRETS_JSON" | grep -o '"AWS_REGION":"[^"]*"' | cut -d'"' -f4)
    export TELEGRAM_BOT_TOKEN=$(echo "$SECRETS_JSON" | grep -o '"TELEGRAM_BOT_TOKEN":"[^"]*"' | cut -d'"' -f4)
    export TELEGRAM_BOT_TOKEN_NORDICE=$(echo "$SECRETS_JSON" | grep -o '"TELEGRAM_BOT_TOKEN_NORDICE":"[^"]*"' | cut -d'"' -f4)
    export TELEGRAM_USER_ID=$(echo "$SECRETS_JSON" | grep -o '"TELEGRAM_USER_ID":"[^"]*"' | cut -d'"' -f4)
    export INSTANCE_TYPE=$(echo "$SECRETS_JSON" | grep -o '"INSTANCE_TYPE":"[^"]*"' | cut -d'"' -f4)
fi

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

# Configuration
KEY_NAME="gascity-key"
SG_NAME="gascity-sg"
INSTANCE_NAME="gascity-server"

# Configure AWS CLI
echo "🔧 Configuring AWS CLI..."
aws configure set aws_access_key_id "$AWS_ACCESS_KEY_ID"
aws configure set aws_secret_access_key "$AWS_SECRET_ACCESS_KEY"
aws configure set region "$AWS_REGION"

# Test AWS
if ! aws sts get-caller-identity &> /dev/null; then
    echo "❌ AWS authentication failed"
    exit 1
fi

echo "✅ AWS credentials validated"

# Create SSH key
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$AWS_REGION" &> /dev/null; then
    echo "🔑 Creating SSH key..."
    aws ec2 create-key-pair \
        --key-name "$KEY_NAME" \
        --region "$AWS_REGION" \
        --query 'KeyMaterial' \
        --output text > "${KEY_NAME}.pem"
    chmod 400 "${KEY_NAME}.pem"
    echo "✅ SSH key created: ${KEY_NAME}.pem"
else
    echo "✅ SSH key already exists"
fi

# Create security group
echo "🔒 Creating security group..."
VPC_ID=$(aws ec2 describe-vpcs --region "$AWS_REGION" --filters "Name=isDefault,Values=true" --query 'Vpcs[0].VpcId' --output text)

SG_ID=$(aws ec2 create-security-group \
    --group-name "$SG_NAME" \
    --description "Gas City" \
    --vpc-id "$VPC_ID" \
    --region "$AWS_REGION" \
    --query 'GroupId' \
    --output text 2>/dev/null || \
    aws ec2 describe-security-groups \
        --region "$AWS_REGION" \
        --filters "Name=group-name,Values=$SG_NAME" \
        --query 'SecurityGroups[0].GroupId' \
        --output text)

echo "✅ Security group: $SG_ID"

# Configure firewall
echo "🔓 Configuring firewall rules..."
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG_ID" --protocol tcp --port 22 --cidr 0.0.0.0/0 2>/dev/null || true
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG_ID" --protocol tcp --port 80 --cidr 0.0.0.0/0 2>/dev/null || true
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG_ID" --protocol tcp --port 443 --cidr 0.0.0.0/0 2>/dev/null || true
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG_ID" --protocol tcp --port 7375 --cidr 0.0.0.0/0 2>/dev/null || true
aws ec2 authorize-security-group-ingress --region "$AWS_REGION" --group-id "$SG_ID" --protocol tcp --port 8080 --cidr 0.0.0.0/0 2>/dev/null || true
echo "✅ Firewall configured"

# Get Ubuntu AMI
echo "🔍 Finding Ubuntu AMI..."
AMI_ID=$(aws ec2 describe-images \
    --region "$AWS_REGION" \
    --owners 099720109477 \
    --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
    --query 'sort_by(Images, &CreationDate)[-1].ImageId' \
    --output text)

echo "✅ AMI: $AMI_ID"

# Create user data script
echo "📝 Creating user data script..."
cat > user-data.sh <<USERDATA
#!/bin/bash
set -e
exec > >(tee /var/log/user-data.log)
exec 2>&1

echo "🚀 Starting Gas City setup..."

# Update system
apt-get update
apt-get upgrade -y

# Install Docker
echo "🐳 Installing Docker..."
curl -fsSL https://get.docker.com | sh
systemctl enable docker
systemctl start docker

# Install Docker Compose
echo "📦 Installing Docker Compose..."
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-\$(uname -s)-\$(uname -m)" -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose

# Create gas city directory
mkdir -p /opt/gascity/config
mkdir -p /opt/gascity/bot
mkdir -p /opt/gascity/gascity/agents/deploy-agent
mkdir -p /opt/gascity/miniapp
mkdir -p /opt/gascity/miniapp-status
cd /opt/gascity

# Create .env file with secrets
cat > .env <<ENV
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_BOT_TOKEN_NORDICE=${TELEGRAM_BOT_TOKEN_NORDICE}
TELEGRAM_USER_ID=${TELEGRAM_USER_ID}
ENV

# Download config files from GitHub
echo "📥 Downloading configuration..."
curl -sL https://raw.githubusercontent.com/Awannaphasch2016/gascity/main/gascity-aws-deploy/docker-compose.yml -o docker-compose.yml
curl -sL https://raw.githubusercontent.com/Awannaphasch2016/gascity/main/gascity-aws-deploy/config/responsibilities.json -o config/responsibilities.json
curl -sL https://raw.githubusercontent.com/Awannaphasch2016/gascity/main/gascity-aws-deploy/bot/telegram_bot.py -o bot/telegram_bot.py
curl -sL https://raw.githubusercontent.com/Awannaphasch2016/gascity/main/gascity-aws-deploy/bot/requirements.txt -o bot/requirements.txt
curl -sL https://raw.githubusercontent.com/Awannaphasch2016/gascity/main/gascity-aws-deploy/bot/Dockerfile -o bot/Dockerfile
curl -sL https://raw.githubusercontent.com/Awannaphasch2016/gascity/main/gascity-aws-deploy/gascity/city.toml -o gascity/city.toml
curl -sL https://raw.githubusercontent.com/Awannaphasch2016/gascity/main/gascity-aws-deploy/gascity/agents/deploy-agent/prompt.md -o gascity/agents/deploy-agent/prompt.md
curl -sL https://raw.githubusercontent.com/Awannaphasch2016/gascity/main/gascity-aws-deploy/miniapp/index.html -o miniapp/index.html

# Start services
echo "🚀 Starting services..."
docker-compose up -d

echo "✅ Gas City deployment complete!"
echo "📊 Services:"
echo "   - Gas City: port 7375"
echo "   - Mini App: port 8080"
echo "   - Telegram bots: running"

# Show status
docker-compose ps
USERDATA

echo "✅ User data script created"

# Launch EC2
echo ""
echo "🚀 Launching EC2 instance..."
echo "   Instance Type: $INSTANCE_TYPE"
echo "   Region: $AWS_REGION"
echo "   AMI: $AMI_ID"
echo ""

INSTANCE_ID=$(aws ec2 run-instances \
    --region "$AWS_REGION" \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --user-data file://user-data.sh \
    --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$INSTANCE_NAME}]" \
    --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}' \
    --query 'Instances[0].InstanceId' \
    --output text)

echo "✅ Instance launched: $INSTANCE_ID"

# Wait for instance to get IP
echo "⏳ Waiting for public IP..."
sleep 10

PUBLIC_IP=$(aws ec2 describe-instances \
    --region "$AWS_REGION" \
    --instance-ids "$INSTANCE_ID" \
    --query 'Reservations[0].Instances[0].PublicIpAddress' \
    --output text)

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║              ✅ DEPLOYMENT COMPLETE!                          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo "📋 Instance Details:"
echo "   Instance ID: $INSTANCE_ID"
echo "   Public IP: $PUBLIC_IP"
echo "   Region: $AWS_REGION"
echo "   Type: $INSTANCE_TYPE"
echo ""
echo "🔑 SSH Access:"
echo "   ssh -i ${KEY_NAME}.pem ubuntu@${PUBLIC_IP}"
echo ""
echo "🌐 Services (will be ready in ~3 minutes):"
echo "   Gas City API: http://${PUBLIC_IP}:7375"
echo "   Mini App: http://${PUBLIC_IP}:8080"
echo ""
echo "🤖 Testing Your Bots:"
echo "   1. Open Telegram"
echo "   2. Find your bot (Social media manager)"
echo "   3. Send: /start"
echo "   4. Should respond: '👋 Welcome, you!'"
echo ""
echo "🔍 Check Logs:"
echo "   ssh -i ${KEY_NAME}.pem ubuntu@${PUBLIC_IP}"
echo "   cd /opt/gascity"
echo "   docker-compose logs -f"
echo ""
echo "📊 Monitor Setup:"
echo "   tail -f /var/log/user-data.log"
echo ""
echo "⏱️  Setup takes ~3 minutes to complete"
echo "   Docker images need to be pulled and built"
echo ""

# Save details to file
cat > deployment-info.txt <<INFO
Gas City Deployment
===================

Instance ID: $INSTANCE_ID
Public IP: $PUBLIC_IP
Region: $AWS_REGION
Instance Type: $INSTANCE_TYPE
SSH Key: ${KEY_NAME}.pem

SSH Command:
ssh -i ${KEY_NAME}.pem ubuntu@${PUBLIC_IP}

Services:
- Gas City: http://${PUBLIC_IP}:7375
- Mini App: http://${PUBLIC_IP}:8080

Test Bots:
Open Telegram → Send /start to your bots
INFO

echo "💾 Deployment info saved to: deployment-info.txt"
echo ""
