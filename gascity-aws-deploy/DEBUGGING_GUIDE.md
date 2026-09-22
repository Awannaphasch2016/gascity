# Gas City Debugging Guide

## Quick Health Checks

```bash
# 1. Check all containers are running
docker-compose ps

# 2. Check Gas City logs
docker-compose logs gascity --tail 100

# 3. Check Telegram bot logs
docker-compose logs telegram-bot --tail 100

# 4. Check if bot can reach Gas City
docker-compose exec telegram-bot curl http://gascity:7375/health

# 5. Follow live logs
docker-compose logs -f
```

## Debugging Telegram Message Flow

### Check if message left Gas City:
```bash
# Gas City logs will show:
docker-compose logs gascity | grep "extmsg"
# Look for: "Sent message to external client"
```

### Check if bot received it:
```bash
# Bot logs will show:
docker-compose logs telegram-bot | grep "SSE"
# Look for: "Received event from Gas City"
```

### Check if Telegram API received it:
```bash
# Bot logs will show:
docker-compose logs telegram-bot | grep "Telegram"
# Look for: "Sent to Telegram API"
```

### Test bot directly:
```bash
# From your phone, send /start to your bot
# Should get immediate response if bot is working
```

## Network Debugging

```bash
# Check ports
docker-compose exec gascity netstat -tlnp | grep 7375

# Check DNS resolution
docker-compose exec telegram-bot ping gascity

# Check API connectivity
curl http://YOUR_EC2_IP:7375/v0/readiness
```

## Common Issues

### Bot not responding?
```bash
# Restart bot only
docker-compose restart telegram-bot

# Check token is valid
docker-compose logs telegram-bot | grep "token"
```

### Gas City not reachable?
```bash
# Check if running
docker ps | grep gascity

# Restart everything
docker-compose restart
```

### Messages not routing?
```bash
# Check responsibilities.json
docker-compose exec telegram-bot cat /app/config/responsibilities.json

# Verify Telegram IDs match
docker-compose logs telegram-bot | grep "telegram_id"
```

## Live Monitoring

```bash
# Watch everything in real-time
docker-compose logs -f --tail 50

# Just Gas City
docker-compose logs -f gascity

# Just bot
docker-compose logs -f telegram-bot
```

## Emergency Access

```bash
# SSH into containers
docker-compose exec gascity bash
docker-compose exec telegram-bot bash

# Check environment variables
docker-compose exec telegram-bot env | grep TELEGRAM

# Manual test Telegram API
docker-compose exec telegram-bot python3 -c "
import requests
TOKEN = 'your-bot-token'
r = requests.get(f'https://api.telegram.org/bot{TOKEN}/getMe')
print(r.json())
"
```
