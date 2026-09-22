# 🚀 QUICK START - Gas City AWS Deployment

## What You Get

✅ Complete Gas City installation on AWS  
✅ Telegram bot with approval routing  
✅ Doppler secrets management  
✅ Auto-restart on reboot  
✅ ~$33/month hosting cost  

## 3-Step Deployment

### 1️⃣ Setup Doppler (2 minutes)

```bash
./setup-doppler.sh
```

This will:
- Install Doppler CLI
- Create project
- Ask for your AWS + Telegram credentials
- Store everything securely

### 2️⃣ Add Your Telegram IDs (1 minute)

Get your Telegram ID:
1. Open Telegram
2. Search for `@userinfobot`
3. Send `/start`
4. Copy your ID

Edit `config/responsibilities.json`:
```json
{
  "users": {
    "you": {
      "telegram_id": 123456789,  # ← Paste YOUR ID here
      ...
    },
    "nordice": {
      "telegram_id": 987654321,  # ← Paste Nordice's ID here
      ...
    }
  }
}
```

### 3️⃣ Deploy! (5 minutes)

```bash
doppler run -- ./deploy.sh
```

**That's it!** The script deploys everything to AWS.

---

## After Deployment

The script will output:

```
✅ DEPLOYMENT SUCCESSFUL!
🌐 Public IP: 54.123.45.67
🔑 SSH Key: gascity-key.pem
```

### Finish Setup:

```bash
# Get your IP from Doppler
PUBLIC_IP=$(doppler secrets get EC2_PUBLIC_IP --plain)

# Upload config files
scp -i gascity-key.pem -r gascity config bot docker-compose.yml ubuntu@${PUBLIC_IP}:/opt/gascity/

# SSH and start
ssh -i gascity-key.pem ubuntu@${PUBLIC_IP}
cd /opt/gascity
doppler run -- docker-compose up -d

# Check logs
docker-compose logs -f
```

---

## Test It!

1. **Find your bot in Telegram**
   - Send `/start`
   - You should see your responsibilities

2. **Agent requests approval**
   - In Gas City, trigger an approval
   - You'll get Telegram message with buttons

3. **Click approve!** ✅

---

## Troubleshooting

**Deployment fails?**
```bash
# Check AWS credentials
doppler secrets

# Verify AWS access
aws sts get-caller-identity
```

**Bot not responding?**
```bash
# Check logs
ssh -i gascity-key.pem ubuntu@${PUBLIC_IP}
docker-compose logs telegram-bot
```

**Can't SSH?**
```bash
# Wait 5 minutes for setup
# Then check instance status
aws ec2 describe-instances \
  --instance-ids $(doppler secrets get EC2_INSTANCE_ID --plain)
```

---

## What's Deployed?

- **EC2 Instance**: Ubuntu 22.04 on t3.medium
- **Gas City**: Latest version with External Messaging API enabled
- **Telegram Bot**: Handles approval routing
- **Docker Compose**: Orchestrates all services
- **Security Groups**: Ports 22, 80, 443, 7375 open
- **Auto-restart**: Systemd service for reliability

---

## Managing Your Deployment

### Update Secrets

```bash
# Rotate Telegram token
doppler secrets set TELEGRAM_BOT_TOKEN="new-token"

# SSH and restart
ssh -i gascity-key.pem ubuntu@${PUBLIC_IP}
cd /opt/gascity
doppler run -- docker-compose restart telegram-bot
```

### View Logs

```bash
ssh -i gascity-key.pem ubuntu@${PUBLIC_IP}
cd /opt/gascity
docker-compose logs -f  # All services
docker-compose logs gascity  # Just Gas City
docker-compose logs telegram-bot  # Just bot
```

### Stop/Start

```bash
# Stop
docker-compose down

# Start
doppler run -- docker-compose up -d
```

### Destroy Everything

```bash
# Terminate EC2 instance
aws ec2 terminate-instances \
  --instance-ids $(doppler secrets get EC2_INSTANCE_ID --plain)

# Delete security group (after termination completes)
aws ec2 delete-security-group --group-name gascity-sg

# Delete SSH key pair
aws ec2 delete-key-pair --key-name gascity-key
rm gascity-key.pem
```

---

## Monthly Cost: ~$33

| Item | Cost |
|------|------|
| EC2 t3.medium | ~$30 |
| EBS 30GB | ~$3 |
| **Total** | **$33** |

Scale down to t3.small (~$15/mo) if needed:
```bash
doppler secrets set INSTANCE_TYPE="t3.small"
doppler run -- ./deploy.sh  # Creates new smaller instance
```

---

## Need Help?

1. Check `README.md` for full documentation
2. Review deployment logs
3. Check AWS Console for instance status
4. Verify Doppler secrets are correct

---

**Ready? Start with:** `./setup-doppler.sh`
