# Gas City AWS Deployment Package

Complete deployment package for Gas City with Telegram bot and Doppler secrets management.

## 📖 Documentation

**IMPORTANT: Read these first!**

- **[infrastructure-overview.html](./infrastructure-overview.html)** — Architecture report of the EC2 deployment (open in a browser)
- **[DEPLOYMENT_SUMMARY.md](./DEPLOYMENT_SUMMARY.md)** ⭐ **START HERE** - Complete overview of what's deployed
- **[FIX_GUIDE.md](./FIX_GUIDE.md)** ⚠️ **ACTION NEEDED** - Fix Flask API (2 min)
- **[DEBUGGING_GUIDE.md](./DEBUGGING_GUIDE.md)** - Troubleshooting guide
- **[MINI_APP_EXPLAINED.md](./MINI_APP_EXPLAINED.md)** - Dashboard architecture

## 📋 Prerequisites

1. **Doppler Account** (get free at doppler.com)
2. **AWS Account** with API credentials
3. **Telegram Bot** (create with @BotFather)
4. **Your Telegram ID** (get from @userinfobot)

## 🚀 Quick Start (5 Minutes)

### Step 1: Install Doppler CLI

**macOS:**
```bash
brew install dopplerhq/cli/doppler
```

**Linux:**
```bash
curl -sLf 'https://packages.doppler.com/public/cli/install.sh' | sh
```

### Step 2: Set Up Doppler Project

```bash
# Login to Doppler
doppler login

# Create project
doppler projects create gascity

# Configure for production
doppler setup --project gascity --config prd
```

### Step 3: Add Your Secrets

```bash
# AWS Credentials
doppler secrets set AWS_ACCESS_KEY_ID="your-aws-key"
doppler secrets set AWS_SECRET_ACCESS_KEY="your-aws-secret"
doppler secrets set AWS_REGION="us-east-1"

# Telegram Bot
doppler secrets set TELEGRAM_BOT_TOKEN="your-bot-token"

# Optional: Customize instance
doppler secrets set INSTANCE_TYPE="t3.medium"  # Default: t3.medium (~$30/mo)

# Verify
doppler secrets
```

### Step 4: Update Telegram IDs

Edit `config/responsibilities.json` and add your Telegram IDs:

```json
{
  "users": {
    "you": {
      "telegram_id": 123456789,  # ← YOUR Telegram ID
      ...
    },
    "nordice": {
      "telegram_id": 987654321,  # ← Nordice's Telegram ID
      ...
    }
  }
}
```

### Step 5: Deploy to AWS! 🚀

```bash
# Make deploy script executable
chmod +x deploy.sh

# Deploy (pulls all secrets from Doppler automatically)
doppler run -- ./deploy.sh
```

That's it! The script will:
- ✅ Create EC2 instance
- ✅ Install Docker
- ✅ Configure security groups
- ✅ Set up auto-restart
- ✅ Store instance info in Doppler

### Step 6: Finish Setup

After deployment completes (5 minutes), run these commands:

```bash
# Get your instance IP from Doppler
PUBLIC_IP=$(doppler secrets get EC2_PUBLIC_IP --plain)

# Upload your config files
scp -i gascity-key.pem -r gascity config bot docker-compose.yml ubuntu@${PUBLIC_IP}:/opt/gascity/

# SSH into server
ssh -i gascity-key.pem ubuntu@${PUBLIC_IP}

# Start services with Doppler
cd /opt/gascity
doppler run -- docker-compose up -d

# Check logs
docker-compose logs -f
```

## 📁 Project Structure

```
gascity-aws-deploy/
├── README.md                    # This file
├── deploy.sh                    # Main deployment script
├── docker-compose.yml           # Container orchestration
├── config/
│   └── responsibilities.json    # User responsibilities config
├── bot/
│   ├── telegram_bot.py          # Telegram HITL bot
│   ├── requirements.txt         # Python dependencies
│   └── Dockerfile               # Bot container
└── gascity/
    ├── city.toml                # Gas City config
    └── agents/
        └── deploy-agent/
            └── prompt.md        # Agent prompt
```

## 🔐 Security Features

- ✅ **No hardcoded secrets** - Everything in Doppler
- ✅ **SSH key auto-generated** and stored securely
- ✅ **Automatic secret rotation** via Doppler
- ✅ **Audit logs** - Know who changed what
- ✅ **Team access** - Share with Nordice safely

## 🎯 How to Use

### View the Mini App Dashboard

Open in your browser:
```
http://YOUR_EC2_IP:8080
```

You'll see a real-time status dashboard showing:
- ✅ Approved sections (with approver name)
- ⏳ Pending sections
- 🚀 Deployment progress

The dashboard auto-refreshes every 2 seconds!

### Test Approval Workflow

1. **Start your Telegram bot:**
   ```
   Search for your bot in Telegram
   Send: /start
   ```

2. **Agent requests approval:**
   Gas City agent sends:
   ```
   APPROVAL_NEEDED: deployment_approval | Deploy v2.0 | Risk: Medium
   ```

3. **You and Nordice receive buttons:**
   ```
   🚀 Deployment Approval Required
   [✅ Approve] [❌ Reject]
   ```

4. **Both approve (requires 2):**
   System proceeds with deployment!

### Manage Secrets

```bash
# View all secrets
doppler secrets

# Update bot token
doppler secrets set TELEGRAM_BOT_TOKEN="new-token"

# Restart bot with new token
ssh -i gascity-key.pem ubuntu@$(doppler secrets get EC2_PUBLIC_IP --plain)
cd /opt/gascity
doppler run -- docker-compose restart telegram-bot
```

### Scale Up/Down

```bash
# Change instance type
doppler secrets set INSTANCE_TYPE="t3.large"

# Re-run deployment (creates new instance)
doppler run -- ./deploy.sh
```

## 💰 Cost Estimate

| Resource | Monthly Cost |
|----------|--------------|
| EC2 t3.medium (2 vCPU, 4GB) | ~$30 |
| EBS 30GB gp3 | ~$3 |
| Data Transfer (100GB/mo) | Free |
| **Total** | **~$33/month** |

## 🔧 Troubleshooting

See `DEBUGGING_GUIDE.md` for complete debugging instructions!

### Quick Health Checks

```bash
# SSH into server
ssh -i gascity-key.pem ubuntu@$(doppler secrets get EC2_PUBLIC_IP --plain)

# Check all containers
docker-compose ps

# View live logs
docker-compose logs -f

# Check if Mini App is accessible
curl http://localhost:8080
```

### Can't connect to instance?

```bash
# Check instance status
aws ec2 describe-instances \
  --instance-ids $(doppler secrets get EC2_INSTANCE_ID --plain) \
  --query 'Reservations[0].Instances[0].State.Name'

# Check security group
aws ec2 describe-security-groups \
  --group-names gascity-sg
```

### Bot not responding?

```bash
# Check bot logs
ssh -i gascity-key.pem ubuntu@$(doppler secrets get EC2_PUBLIC_IP --plain)
docker-compose logs telegram-bot
```

### Gas City not starting?

```bash
# Check Gas City logs
docker-compose logs gascity

# Restart services
doppler run -- docker-compose restart
```

## 📚 Next Steps

- [ ] Add more responsibility types in `config/responsibilities.json`
- [ ] Create custom formulas in `gascity/formulas/`
- [ ] Set up SSL with Let's Encrypt
- [ ] Configure webhook for GitHub/GitLab
- [ ] Add more team members

## 🆘 Support

- Gas City Docs: https://gascity.sh
- Doppler Docs: https://docs.doppler.com
- Telegram Bot API: https://core.telegram.org/bots/api

## 📄 License

MIT
