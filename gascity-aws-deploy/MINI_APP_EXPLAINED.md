# 🔍 Debugging & Mini App Setup - Complete Guide

## ✅ ANSWERS TO YOUR QUESTIONS

### 1. How to Investigate Errors?

**WE'RE USING DOCKER COMPOSE (NOT KUBERNETES!)**

Stack:
```
EC2 Instance (Ubuntu)
├── Docker
├── Docker Compose
├── Container 1: Gas City
├── Container 2: Telegram Bot (You)
├── Container 3: Telegram Bot (Nordice)
└── Container 4: Mini App (nginx)
```

#### Debugging Tools You Have:

**A) Check if message left Gas City:**
```bash
docker-compose logs gascity | grep "extmsg"
# Look for: "Sent message to external client"
```

**B) Check if bot received it:**
```bash
docker-compose logs telegram-bot | grep "SSE"
# Look for: "Received event from Gas City"
```

**C) Check if Telegram API received it:**
```bash
docker-compose logs telegram-bot | grep "Telegram"
# Look for: "Sent to Telegram API: 200 OK"
```

**D) Test bot directly:**
- Send `/start` to your bot from your phone
- If it responds immediately = bot works!

**E) Live monitoring (see everything):**
```bash
docker-compose logs -f
```

**F) SSH into containers:**
```bash
docker-compose exec gascity bash
docker-compose exec telegram-bot bash
```

See `DEBUGGING_GUIDE.md` for the complete reference!

---

### 2. Mini App Infrastructure Requirements

**ANSWER: ALMOST NOTHING NEEDED! ✨**

What's Required:
- ✅ Simple nginx container (already in docker-compose.yml)
- ✅ HTML + JS file (already created in miniapp/index.html)
- ✅ Port 8080 open (already added to security group)
- ✅ Shared volume for status files (already configured)

What's **NOT** Required:
- ❌ No external hosting service
- ❌ No database
- ❌ No special infrastructure
- ❌ No HTTPS setup (optional)
- ❌ No domain name

**It's just a playground UI!** 🎨

---

## 🎨 How the Mini App Works

### Architecture:

```
┌─────────────────────────────────────────────────────┐
│  Telegram Bot (telegram_bot.py)                     │
│  When approval happens:                             │
│  └─> Writes JSON file to /app/status/hero.json     │
└──────────────────┬──────────────────────────────────┘
                   │
                   │ Shared Volume: miniapp-status/
                   │
┌──────────────────▼──────────────────────────────────┐
│  Nginx Container (miniapp)                          │
│  Serves:                                            │
│  ├─ index.html (from ./miniapp/)                    │
│  └─ status/*.json (from ./miniapp-status/)          │
└─────────────────────────────────────────────────────┘
                   │
                   │
┌──────────────────▼──────────────────────────────────┐
│  Browser (Your Phone/Computer)                      │
│  http://YOUR_EC2_IP:8080                            │
│  JavaScript polls status/*.json every 2 seconds     │
│  Shows: "✅ Hero Section (Approved by You)"         │
└─────────────────────────────────────────────────────┘
```

### What Happens Step by Step:

1. **Gas City agent needs approval:**
   ```
   APPROVAL_NEEDED: deployment_approval | Hero Section | ...
   ```

2. **Bot parses "Hero Section":**
   - Maps to filename: `hero.json`
   - Sends to your Telegram
   - Shows [✅ Approve] [❌ Reject] buttons

3. **You click ✅ Approve:**
   - Bot writes to `/app/status/hero.json`:
     ```json
     {
       "status": "approved",
       "approved_by": "you",
       "approved_at": "2026-09-22T14:30:00Z"
     }
     ```

4. **Mini App polls every 2 seconds:**
   ```javascript
   fetch('/status/hero.json')
   ```

5. **Mini App updates UI:**
   ```
   ✅ Hero Section
      Approved by: you
      Status: Deployed
   ```

**ALL REAL-TIME, NO COMPLEX SETUP!** 🚀

---

## 🎯 What You Can Observe

Open `http://YOUR_EC2_IP:8080` in your browser:

```
┌────────────────────────────────────────────────────┐
│         Landing Page Build Status                  │
├────────────────────────────────────────────────────┤
│                                                    │
│  ✅ Hero Section                                   │
│     Approved by: You                               │
│     Status: Deployed                               │
│                                                    │
│  ⏳ Features Section                               │
│     Status: Awaiting approval                      │
│                                                    │
│  ✅ Auth Form                                      │
│     Approved by: Nordice                           │
│     Status: Deployed                               │
│                                                    │
│  ⏳ Final Deployment                               │
│     Needs: 2 approvals (0/2)                       │
│                                                    │
│  Last updated: 2:30:15 PM                          │
└────────────────────────────────────────────────────┘
```

**Perfect for your "playground UI" requirement!** 🎨

---

## 🔍 Debugging Flow

When something goes wrong, follow this flow:

```
1. Check if containers are running:
   $ docker-compose ps
   ✅ All should be "Up"

2. Check Gas City health:
   $ curl http://localhost:7375/health
   ✅ Should return {"status":"ok"}

3. Check if bot registered with Gas City:
   $ docker-compose logs telegram-bot | grep "Registered"
   ✅ Should see: "✅ Registered with Gas City"

4. Test Telegram bot:
   Send /start from your phone
   ✅ Should get immediate response

5. Check if approvals are being written:
   $ ls -la miniapp-status/
   ✅ Should see .json files after approvals

6. Check Mini App:
   $ curl http://localhost:8080
   ✅ Should return HTML

7. Check if Mini App can read status:
   $ curl http://localhost:8080/status/hero.json
   ✅ Should return JSON (after approval)
```

**Each step isolates the problem!** 🎯

---

## 📊 Files Created

Here's what's been added to your deployment package:

### New Files:
```
gascity-aws-deploy/
├── DEBUGGING_GUIDE.md          # Complete debugging reference
├── miniapp/
│   └── index.html              # Mini App UI (playground)
└── miniapp-status/             # Created by docker-compose
    ├── hero.json               # Written when Hero approved
    ├── features.json           # Written when Features approved
    ├── auth.json               # Written when Auth approved
    ├── footer.json             # Written when Footer approved
    └── deployment.json         # Written when Deployment approved
```

### Updated Files:
```
✅ docker-compose.yml           # Added miniapp container + volumes
✅ deploy.sh                    # Added port 8080 to security group
✅ bot/telegram_bot.py          # Added write_miniapp_status()
✅ README.md                    # Added Mini App instructions
```

---

## 🚀 READY TO DEPLOY?

Everything is set up for:

1. **Simple Debugging**
   - Just SSH + docker logs
   - No Kubernetes complexity
   - Easy to understand

2. **Observable Demo**
   - Real-time status dashboard
   - No external dependencies
   - Perfect for testing

3. **Two-User Workflow**
   - You and Nordice get separate bots
   - Responsibility-based routing
   - Multi-approval support

**Just say "START" and I'll deploy it all!** 🎯
