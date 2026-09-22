# 🔧 Fix Guide: Enable Test Approvals

## Current Status

### ✅ What's Working
- EC2 instance running (13.214.162.41)
- Both Telegram bots running and responding to `/start`
- Mini App dashboard accessible at http://13.214.162.41:8080
- Port 7375 is open
- Responsibilities config is loaded

### ❌ What's Broken
- Flask API returns 500 errors when receiving messages
- Approval requests cannot be routed to bots
- The issue is in `/opt/gascity/simple_gc_api.py` - missing error handling

## Quick Fix

### Option 1: Replace Flask App (Recommended, 2 minutes)

```bash
# SSH into EC2
ssh ubuntu@13.214.162.41

# Stop current Flask app
pkill -f simple_gc_api.py

# Download fixed version
cd /opt/gascity
wget https://raw.githubusercontent.com/YOUR-REPO/main/simple_gc_api_fixed.py -O simple_gc_api_fixed.py

# Or copy from local file:
# Upload simple_gc_api_fixed.py to the server, then:

# Start fixed Flask app
nohup python3 simple_gc_api_fixed.py > flask.log 2>&1 &

# Check it's running
curl http://localhost:7375/health

# Check logs
tail -f flask.log
```

### Option 2: Manual Fix (Edit existing file)

SSH in and edit `/opt/gascity/simple_gc_api.py`:

1. Add proper exception handling in the `receive_message()` function
2. Add logging to see what's causing the 500 error  
3. Load the responsibilities config at startup
4. Implement message queue system

See `simple_gc_api_fixed.py` for the complete working code.

## Test After Fix

### 1. Check API Health

```bash
curl http://13.214.162.41:7375/health
```

Should return: `{"status":"ok","clients":N}`

### 2. Send Test Approvals

From your local machine:

```bash
python3 send-test-approvals.py
```

This will send 3 test approval requests:
- ✅ Deployment Approval → Both you and Karant
- ✅ Security Review → Karant only
- ✅ Budget Approval → You only

### 3. Verify in Telegram

Check both bots:
- **YOUR Bot** (`@yourbot`): Should receive Deployment and Budget approvals
- **KARANT Bot** (`@Karant_is_my_bot`): Should receive Deployment and Security approvals

### 4. Click Approve/Deny

When you click a button:
- Bot should respond with confirmation
- Mini App should update (check http://13.214.162.41:8080)
- Status JSON files should be created in `/opt/gascity/miniapp-status/`

## Expected Full Flow

```
1. send-test-approvals.py sends approval request
   ↓
2. Flask API (/v0/extmsg/inbound) receives it
   ↓
3. Flask parses APPROVAL_NEEDED format
   ↓
4. Flask routes to appropriate user(s) based on responsibilities.json
   ↓
5. Flask queues message for bot(s)
   ↓
6. Bot polls Flask API (/v0/extmsg/outbound)
   ↓
7. Bot receives approval request
   ↓
8. Bot sends Telegram message with Approve/Deny buttons
   ↓
9. User clicks button in Telegram
   ↓
10. Bot processes button click
    ↓
11. Bot writes status to miniapp-status/*.json
    ↓
12. Mini App polls and displays updated status
```

## Debugging

### Check Flask Logs

```bash
ssh ubuntu@13.214.162.41
tail -f /opt/gascity/flask.log
```

### Check Bot Logs

```bash
# Your bot
tail -f /opt/gascity/bot-you.log

# Karant bot  
tail -f /opt/gascity/bot-nordice.log
```

### Check Mini App Status Files

```bash
ls -la /opt/gascity/miniapp-status/
cat /opt/gascity/miniapp-status/deployment_approval.json
```

### Test Individual Components

```bash
# Test Flask API
curl http://13.214.162.41:7375/health

# Test bot registration
curl -X POST http://13.214.162.41:7375/v0/extmsg/clients \
  -H "Content-Type: application/json" \
  -d '{"agent_name":"test"}'

# Test message routing  
curl -X POST http://13.214.162.41:7375/v0/extmsg/inbound \
  -H "Content-Type: application/json" \
  -d '{"client_id":"test","message":"Hello"}'
```

## Files Included

- `simple_gc_api_fixed.py` - Fixed Flask API with proper error handling
- `send-test-approvals.py` - Script to trigger test approvals
- `enable-test-triggers.sh` - Script to open port 7375 (already done)

## What You've Built

This is a working **multi-tenant software factory** with:

1. **Multi-user HITL**: Two users (You and Karant) with different responsibilities
2. **Responsibility routing**: Approvals go to the right people
3. **Interactive interface**: Telegram bots with approve/deny buttons
4. **Observable outcomes**: Mini App shows approval status in real-time
5. **Scalable architecture**: One EC2 instance per tenant (~$33/month each)

## Next Steps

After fixing the Flask app:

1. **Test the full flow** - Send approvals, click buttons, see Mini App update
2. **Add more approval types** - Extend `responsibilities.json`
3. **Connect real Gas Town** - Replace mock API with full Gas Town
4. **Add more users** - Scale to your team
5. **Production deploy** - Add monitoring, backups, SSL, custom domain

## Need Help?

If Flask app still returns 500 after fix:
1. Check Flask logs for the specific error
2. Verify responsibilities.json is readable at `/app/config/responsibilities.json`
3. Check Python dependencies: `pip3 install flask flask-cors`
4. Restart both bots after Flask fix

The infrastructure is solid - just needs this one Flask app fix to complete the demo! 🚀
