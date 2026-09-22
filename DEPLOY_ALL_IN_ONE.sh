#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# GAS CITY AWS DEPLOYMENT - ALL-IN-ONE SCRIPT
# Copy this entire file and save as: deploy-gascity.sh
# ═══════════════════════════════════════════════════════════════════

set -e

echo "🚀 Gas City Complete Deployment"
echo "================================"
echo ""

# ───────────────────────────────────────────────────────────────────
# STEP 1: Install Prerequisites
# ───────────────────────────────────────────────────────────────────

echo "📦 Installing prerequisites..."

# Install Doppler CLI
if ! command -v doppler &> /dev/null; then
    echo "Installing Doppler CLI..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install dopplerhq/cli/doppler
    else
        curl -sLf 'https://packages.doppler.com/public/cli/install.sh' | sh
    fi
fi

# Install AWS CLI
if ! command -v aws &> /dev/null; then
    echo "Installing AWS CLI..."
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install awscli
    else
        curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
        unzip -q awscliv2.zip
        sudo ./aws/install
        rm -rf aws awscliv2.zip
    fi
fi

# Install jq
if ! command -v jq &> /dev/null; then
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install jq
    else
        sudo apt-get update && sudo apt-get install -y jq
    fi
fi

echo "✅ Prerequisites installed"

# ───────────────────────────────────────────────────────────────────
# STEP 2: Setup Doppler
# ───────────────────────────────────────────────────────────────────

echo ""
echo "🔐 Doppler Setup"
echo "────────────────"

doppler login
doppler projects create gascity 2>/dev/null || echo "Project exists"
doppler setup --project gascity --config prd

echo ""
echo "Please provide your secrets:"
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

read -p "Your Telegram User ID: " YOUR_TG_ID
doppler secrets set YOUR_TELEGRAM_ID="$YOUR_TG_ID"

read -p "Nordice's Telegram User ID: " NORDICE_TG_ID
doppler secrets set NORDICE_TELEGRAM_ID="$NORDICE_TG_ID"

read -p "Instance Type (default: t3.medium): " INSTANCE_TYPE
INSTANCE_TYPE=${INSTANCE_TYPE:-t3.medium}
doppler secrets set INSTANCE_TYPE="$INSTANCE_TYPE"

# ───────────────────────────────────────────────────────────────────
# STEP 3: Create Project Structure
# ───────────────────────────────────────────────────────────────────

echo ""
echo "📁 Creating project structure..."

mkdir -p gascity-deploy/{bot,config,gascity/agents/deploy-agent}

# Create responsibilities.json
cat > gascity-deploy/config/responsibilities.json <<'RESP_JSON'
{
  "users": {
    "you": {
      "telegram_id": YOUR_TG_ID_PLACEHOLDER,
      "telegram_username": "@yourname",
      "responsibilities": ["deployment_approval", "architecture_review", "budget_approval"]
    },
    "nordice": {
      "telegram_id": NORDICE_TG_ID_PLACEHOLDER,
      "telegram_username": "@nordice",
      "responsibilities": ["security_review", "code_review", "deployment_approval"]
    }
  },
  "responsibility_definitions": {
    "deployment_approval": {
      "name": "Deployment Approval",
      "description": "Approve production deployments",
      "icon": "🚀",
      "requires_multiple": true,
      "required_count": 2
    },
    "security_review": {
      "name": "Security Review",
      "description": "Review security implications",
      "icon": "🔒",
      "requires_multiple": false
    },
    "code_review": {
      "name": "Code Review",
      "description": "Review code changes",
      "icon": "👨‍💻",
      "requires_multiple": false
    },
    "architecture_review": {
      "name": "Architecture Review",
      "description": "Review architectural decisions",
      "icon": "🏗️",
      "requires_multiple": false
    },
    "budget_approval": {
      "name": "Budget Approval",
      "description": "Approve spending",
      "icon": "💰",
      "requires_multiple": false
    }
  }
}
RESP_JSON

# Replace placeholders
sed -i.bak "s/YOUR_TG_ID_PLACEHOLDER/$YOUR_TG_ID/" gascity-deploy/config/responsibilities.json
sed -i.bak "s/NORDICE_TG_ID_PLACEHOLDER/$NORDICE_TG_ID/" gascity-deploy/config/responsibilities.json
rm -f gascity-deploy/config/responsibilities.json.bak

# Create Telegram bot
cat > gascity-deploy/bot/telegram_bot.py <<'PYEOF'
import json, logging, os, requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from sseclient import SSEClient
import threading
from datetime import datetime
from typing import Dict, List, Optional

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GC_API = os.getenv("GC_API", "http://gascity:7375")
CONFIG_PATH = "/app/config/responsibilities.json"

with open(CONFIG_PATH) as f:
    RESPONSIBILITIES = json.load(f)

GC_CLIENT_ID = None
GC_TOKEN = None
active_approvals: Dict[str, dict] = {}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class ResponsibilityRouter:
    def __init__(self, config):
        self.config = config
        self.users = config["users"]
        self.definitions = config["responsibility_definitions"]
    
    def get_approvers_for_responsibility(self, responsibility: str) -> List[dict]:
        approvers = []
        for username, user_data in self.users.items():
            if responsibility in user_data["responsibilities"]:
                approvers.append({
                    "username": username,
                    "telegram_id": user_data["telegram_id"],
                    "telegram_username": user_data.get("telegram_username", username)
                })
        return approvers

router = ResponsibilityRouter(RESPONSIBILITIES)

def register_with_gascity():
    global GC_CLIENT_ID, GC_TOKEN
    try:
        response = requests.post(f"{GC_API}/v0/extmsg/clients", 
                                 json={"provider": "llm-client", "display_name": "telegram-hitl-bot"},
                                 headers={"X-GC-Request": "1"}, timeout=10)
        response.raise_for_status()
        data = response.json()
        GC_CLIENT_ID = data["client_id"]
        GC_TOKEN = data["token"]
        logger.info(f"✅ Registered: {GC_CLIENT_ID}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed: {e}")
        return False

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("👋 Gas City Approval Bot Ready!")

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("✅ Processed!")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Message received")

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ No token")
        return
    if not register_with_gascity():
        return
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
PYEOF

# Create bot requirements
cat > gascity-deploy/bot/requirements.txt <<'REQEOF'
python-telegram-bot==20.7
requests==2.31.0
sseclient-py==1.8.0
REQEOF

# Create bot Dockerfile
cat > gascity-deploy/bot/Dockerfile <<'DOCKEOF'
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY telegram_bot.py .
CMD ["python", "telegram_bot.py"]
DOCKEOF

# Create docker-compose.yml
cat > gascity-deploy/docker-compose.yml <<'COMPEOF'
version: '3.8'
services:
  gascity:
    image: gastownhall/gascity:latest
    container_name: gascity
    ports:
      - "7375:7375"
    volumes:
      - ./gascity:/city
      - gascity-data:/city/.gc
    environment:
      - GC_CITY_PATH=/city
    restart: unless-stopped
    command: gc start
  telegram-bot:
    build: ./bot
    container_name: telegram-bot
    depends_on:
      - gascity
    environment:
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - GC_API=http://gascity:7375
    volumes:
      - ./config:/app/config
    restart: unless-stopped
volumes:
  gascity-data:
COMPEOF

# Create Gas City config
cat > gascity-deploy/gascity/city.toml <<'CITYEOF'
[city]
name = "my-city"

[session]
provider = "tmux"

[extmsg]
enabled = true

[[agent]]
name = "deploy-agent"
provider = "claude"
prompt_file = "agents/deploy-agent/prompt.md"
dir = "."
CITYEOF

# Create agent prompt
cat > gascity-deploy/gascity/agents/deploy-agent/prompt.md <<'PROMPTEOF'
You are a deployment agent. When you need approval, format messages like:

APPROVAL_NEEDED: responsibility_type | title | details

Types: deployment_approval, security_review, code_review, architecture_review, budget_approval

Wait for APPROVAL_GRANTED or APPROVAL_REJECTED before proceeding.
PROMPTEOF

echo "✅ Project structure created"

# ───────────────────────────────────────────────────────────────────
# STEP 4: Deploy to AWS
# ───────────────────────────────────────────────────────────────────

echo ""
echo "🚀 Deploying to AWS..."

# Load secrets
export $(doppler secrets download --no-file --format env)

# Configure AWS
aws configure set aws_access_key_id "$AWS_ACCESS_KEY_ID"
aws configure set aws_secret_access_key "$AWS_SECRET_ACCESS_KEY"
aws configure set region "$AWS_REGION"

KEY_NAME="gascity-key"
SG_NAME="gascity-sg"

# Create SSH key
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$AWS_REGION" &>/dev/null; then
    aws ec2 create-key-pair --key-name "$KEY_NAME" --region "$AWS_REGION" \
        --query 'KeyMaterial' --output text > "${KEY_NAME}.pem"
    chmod 400 "${KEY_NAME}.pem"
    doppler secrets set SSH_PRIVATE_KEY="$(cat ${KEY_NAME}.pem)" --silent
fi

# Create security group
VPC_ID=$(aws ec2 describe-vpcs --region "$AWS_REGION" --filters "Name=isDefault,Values=true" \
         --query 'Vpcs[0].VpcId' --output text)

SG_ID=$(aws ec2 create-security-group --group-name "$SG_NAME" --description "Gas City" \
        --vpc-id "$VPC_ID" --region "$AWS_REGION" --query 'GroupId' --output text 2>/dev/null || \
        aws ec2 describe-security-groups --region "$AWS_REGION" \
        --filters "Name=group-name,Values=$SG_NAME" --query 'SecurityGroups[0].GroupId' --output text)

# Configure firewall
for port in 22 80 443 7375; do
    aws ec2 authorize-security-group-ingress --region "$AWS_REGION" \
        --group-id "$SG_ID" --protocol tcp --port $port --cidr 0.0.0.0/0 2>/dev/null || true
done

# Get Ubuntu AMI
AMI_ID=$(aws ec2 describe-images --region "$AWS_REGION" --owners 099720109477 \
         --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
         --query 'sort_by(Images, &CreationDate)[-1].ImageId' --output text)

# User data
cat > user-data.sh <<'USEREOF'
#!/bin/bash
apt-get update && apt-get upgrade -y
curl -fsSL https://get.docker.com | sh
curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" \
     -o /usr/local/bin/docker-compose
chmod +x /usr/local/bin/docker-compose
mkdir -p /opt/gascity
USEREOF

# Launch instance
INSTANCE_ID=$(aws ec2 run-instances --region "$AWS_REGION" --image-id "$AMI_ID" \
              --instance-type "$INSTANCE_TYPE" --key-name "$KEY_NAME" \
              --security-group-ids "$SG_ID" --user-data file://user-data.sh \
              --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=gascity-server}]" \
              --block-device-mappings 'DeviceName=/dev/sda1,Ebs={VolumeSize=30,VolumeType=gp3}' \
              --query 'Instances[0].InstanceId' --output text)

doppler secrets set EC2_INSTANCE_ID="$INSTANCE_ID" --silent

echo "⏳ Waiting for instance..."
aws ec2 wait instance-running --region "$AWS_REGION" --instance-ids "$INSTANCE_ID"

PUBLIC_IP=$(aws ec2 describe-instances --region "$AWS_REGION" --instance-ids "$INSTANCE_ID" \
            --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

doppler secrets set EC2_PUBLIC_IP="$PUBLIC_IP" --silent

rm -f user-data.sh

echo ""
echo "═══════════════════════════════════════════"
echo "✅ DEPLOYMENT SUCCESSFUL!"
echo "═══════════════════════════════════════════"
echo ""
echo "🌐 Public IP: $PUBLIC_IP"
echo "🔑 SSH Key: ${KEY_NAME}.pem"
echo ""
echo "Next: Wait 5 minutes, then run:"
echo ""
echo "  scp -i ${KEY_NAME}.pem -r gascity-deploy/* ubuntu@${PUBLIC_IP}:/opt/gascity/"
echo "  ssh -i ${KEY_NAME}.pem ubuntu@${PUBLIC_IP}"
echo "  cd /opt/gascity"
echo "  docker-compose up -d"
echo ""
