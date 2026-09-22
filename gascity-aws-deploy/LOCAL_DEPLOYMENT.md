# 🚀 Local Deployment Guide

You've downloaded the Gas City deployment package! Here's how to deploy it from your machine.

## 📋 Prerequisites

- **macOS or Linux** (Windows WSL works too)
- **Doppler account** (free at doppler.com)
- **AWS account** with API credentials
- **Telegram bot** (create with @BotFather)

## 🎯 Quick Start (5 Minutes)

### Step 1: Install Doppler CLI

**macOS:**
```bash
brew install dopplerhq/cli/doppler
```

**Linux:**
```bash
curl -sLf 'https://packages.doppler.com/public/cli/install.sh' | sh
```

### Step 2: Run Setup Script

```bash
cd gascity-aws-deploy
./setup-doppler.sh
```

This will:
- ✅ Login to Doppler
- ✅ Create project
- ✅ Prompt for AWS credentials
- ✅ Prompt for Telegram bot token
- ✅ Store everything securely

**Secrets you'll need:**
- AWS Access Key ID
- AWS Secret Access Key
- AWS Region (e.g., us-east-1)
- Telegram Bot Token (from @BotFather)

### Step 3: Add Your Telegram IDs

Get your Telegram ID:
```
1. Open Telegram
2. Search for @userinfobot
3. Send: /start
4. Copy your ID (e.g., 123456789)
```

Edit the config file:
```bash
nano config/responsibilities.json
```

Update these lines:
```json
{
  "users": {
    "you": {
      "telegram_id": 123456789,  # ← YOUR ID HERE
      ...
    },
    "nordice": {
      "telegram_id": 987654321,  # ← NORDICE'S ID HERE
      ...
    }
  }
}
```

Save and exit (Ctrl+X, then Y, then Enter).

### Step 4: Deploy to AWS! 🚀

```bash
doppler run -- ./deploy.sh
```

The script will:
1. ✅ Validate your AWS credentials
2. ✅ Create EC2 instance (Ubuntu 22.04)
3. ✅ Install Docker + Docker Compose
4. ✅ Configure security groups
5. ✅ Generate SSH key
6. ✅ Set up auto-restart service
7. ✅ Return your public IP

**Wait 5-10 minutes** for the deployment to complete.

### Step 5: Finish Setup

After deployment, you'll see:
```
✅ DEPLOYMENT SUCCESSFUL!
🌐 Public IP: 54.123.45.67
🔑 SSH Key: gascity-key.pem
```

Now upload your config files:
```bash
# Get IP from Doppler
PUBLIC_IP=$(doppler secrets get EC2_PUBLIC_IP --plain)

# Upload files
scp -i gascity-key.pem -r gascity config bot docker-compose.yml ubuntu@${PUBLIC_IP}:/opt/gascity/

# SSH into server
ssh -i gascity-key.pem ubuntu@${PUBLIC_IP}

# Start services
cd /opt/gascity
doppler run -- docker-compose up -d

# Check logs
docker-compose logs -f
```

### Step 6: Test Your Bot

1. Open Telegram
2. Search for your bot (username from @BotFather)
3. Send: `/start`
4. You should see your responsibilities!

## 🎯 What Happens Next

Once deployed:

1. **Gas City** runs on `http://YOUR_IP:7375`
2. **Telegram bot** listens for approval requests
3. **Agents** can request approvals via formatted messages
4. **You and Nordice** receive buttons in Telegram
5. **Approvals** are routed based on responsibilities

Example approval flow:
```
Agent: "APPROVAL_NEEDED: deployment_approval | Deploy v2.0 | Risk: Medium"
  ↓
Bot routes to you AND Nordice (requires 2 approvals)
  ↓
Both click ✅
  ↓
Agent proceeds with deployment!
```

## 💰 Cost

| Resource | Monthly Cost |
|----------|--------------|
| EC2 t3.medium | ~$30 |
| EBS 30GB | ~$3 |
| **Total** | **~$33** |

Want to save money? Use t3.small (~$15/mo):
```bash
doppler secrets set INSTANCE_TYPE="t3.small"
doppler run -- ./deploy.sh
```

## 🔧 Troubleshooting

### "doppler: command not found"

Install Doppler CLI (see Step 1 above).

### "AWS authentication failed"

Check your credentials:
```bash
doppler secrets get AWS_ACCESS_KEY_ID
doppler secrets get AWS_SECRET_ACCESS_KEY
aws sts get-caller-identity
```

### "SSH connection refused"

Wait 5-10 minutes for setup to complete, then try again:
```bash
ssh -i gascity-key.pem ubuntu@${PUBLIC_IP}
```

### Bot not responding?

Check logs:
```bash
ssh -i gascity-key.pem ubuntu@${PUBLIC_IP}
cd /opt/gascity
docker-compose logs telegram-bot
```

### Gas City not starting?

```bash
docker-compose logs gascity
docker-compose restart gascity
```

## 🛠️ Managing Your Deployment

### Update Secrets

```bash
doppler secrets set TELEGRAM_BOT_TOKEN="new-token"
ssh -i gascity-key.pem ubuntu@${PUBLIC_IP}
cd /opt/gascity
doppler run -- docker-compose restart telegram-bot
```

### View All Logs

```bash
docker-compose logs -f
```

### Stop Services

```bash
docker-compose down
```

### Start Services

```bash
doppler run -- docker-compose up -d
```

### Destroy Everything

```bash
# Terminate instance
aws ec2 terminate-instances \
  --instance-ids $(doppler secrets get EC2_INSTANCE_ID --plain)

# Delete security group (after instance terminates)
aws ec2 delete-security-group --group-name gascity-sg

# Delete SSH key
aws ec2 delete-key-pair --key-name gascity-key
rm gascity-key.pem
```

## 📚 Next Steps

- [ ] Add more team members to `config/responsibilities.json`
- [ ] Create custom formulas in `gascity/formulas/`
- [ ] Set up SSL with Let's Encrypt
- [ ] Configure webhooks for GitHub/GitLab
- [ ] Add more approval workflows

## 📖 Documentation

- `README.md` - Full documentation
- `QUICKSTART.md` - Quick walkthrough
- `DEPLOYMENT_SUMMARY.md` - Architecture overview

## 🆘 Need Help?

All files are documented. If you get stuck:
1. Check the troubleshooting section above
2. Review deployment logs
3. Verify Doppler secrets are correct
4. Check AWS Console for instance status

---

**Ready to deploy?** Start with `./setup-doppler.sh`! 🚀
