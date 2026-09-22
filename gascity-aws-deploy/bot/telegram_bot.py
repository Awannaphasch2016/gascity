#!/usr/bin/env python3
"""
Telegram HITL Bot for Gas City
Handles responsibility-based approval routing
"""
import json
import logging
import os
import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, 
    MessageHandler, filters, ContextTypes
)
from sseclient import SSEClient
import threading
from datetime import datetime
from typing import Dict, List, Optional

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
GC_API = os.getenv("GC_API", "http://gascity:7375")
CONFIG_PATH = "/app/config/responsibilities.json"

# Load responsibility config
with open(CONFIG_PATH, "r") as f:
    RESPONSIBILITIES = json.load(f)

# Gas City credentials
GC_CLIENT_ID = None
GC_TOKEN = None

# Active approval requests
active_approvals: Dict[str, dict] = {}

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def write_miniapp_status(section_title: str, approved_by: str, status: str):
    """Write approval status to Mini App JSON files"""
    try:
        # Map section titles to file names
        section_mapping = {
            'Hero Section': 'hero',
            'Features Section': 'features',
            'Auth Form': 'auth',
            'Footer Section': 'footer',
            'Final Deployment': 'deployment'
        }
        
        section_id = section_mapping.get(section_title)
        if not section_id:
            logger.warning(f"Unknown section: {section_title}")
            return
        
        status_dir = "/app/status"
        os.makedirs(status_dir, exist_ok=True)
        
        status_data = {
            "status": status,
            "approved_by": approved_by,
            "approved_at": datetime.now().isoformat()
        }
        
        status_file = os.path.join(status_dir, f"{section_id}.json")
        with open(status_file, 'w') as f:
            json.dump(status_data, f, indent=2)
        
        logger.info(f"✅ Wrote status for {section_title} to {status_file}")
    except Exception as e:
        logger.error(f"❌ Failed to write Mini App status: {e}")

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
    
    def requires_multiple_approvals(self, responsibility: str) -> tuple[bool, int]:
        defn = self.definitions.get(responsibility, {})
        requires_multiple = defn.get("requires_multiple", False)
        required_count = defn.get("required_count", 1)
        return requires_multiple, required_count
    
    def get_responsibility_metadata(self, responsibility: str) -> dict:
        return self.definitions.get(responsibility, {
            "name": responsibility,
            "description": "",
            "icon": "📋"
        })

router = ResponsibilityRouter(RESPONSIBILITIES)

def register_with_gascity():
    global GC_CLIENT_ID, GC_TOKEN
    try:
        response = requests.post(
            f"{GC_API}/v0/extmsg/clients",
            json={"provider": "llm-client", "display_name": "telegram-hitl-bot"},
            headers={"X-GC-Request": "1"},
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        GC_CLIENT_ID = data["client_id"]
        GC_TOKEN = data["token"]
        logger.info(f"✅ Registered with Gas City: {GC_CLIENT_ID}")
        return True
    except Exception as e:
        logger.error(f"❌ Failed to register: {e}")
        return False

def send_to_gascity(conversation_id: str, message: str):
    try:
        response = requests.post(
            f"{GC_API}/v0/extmsg/inbound",
            json={
                "provider": "llm-client",
                "account_id": GC_CLIENT_ID,
                "conversation_id": conversation_id,
                "content": message
            },
            headers={"Content-Type": "application/json", "X-GC-Request": "1"},
            timeout=10
        )
        response.raise_for_status()
    except Exception as e:
        logger.error(f"❌ Send failed: {e}")

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = get_username_from_telegram_id(update.effective_user.id)
    if username:
        responsibilities = RESPONSIBILITIES["users"][username]["responsibilities"]
        resp_list = "\n".join([f"• {r}" for r in responsibilities])
        await update.message.reply_text(
            f"👋 Welcome, {username}!\n\nYour responsibilities:\n{resp_list}\n\n"
            "You'll receive approval requests here."
        )
    else:
        await update.message.reply_text(
            "❌ Not registered. Add your Telegram ID to config/responsibilities.json"
        )

def get_username_from_telegram_id(telegram_id: int) -> Optional[str]:
    for username, user_data in RESPONSIBILITIES["users"].items():
        if user_data["telegram_id"] == telegram_id:
            return username
    return None

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = json.loads(query.data)
    action = data["action"]
    approval_id = data.get("approval_id")
    username = get_username_from_telegram_id(update.effective_user.id)
    
    if not username:
        await query.edit_message_text("❌ User not registered")
        return
    
    if action in ["approve", "reject"]:
        approval = active_approvals.get(approval_id)
        if not approval:
            await query.edit_message_text("❌ Approval expired")
            return
        
        if username not in approval["approvers"]:
            await query.edit_message_text("❌ Not authorized")
            return
        
        if username in approval["responses"]:
            await query.edit_message_text(f"⚠️ Already {approval['responses'][username]}")
            return
        
        approval["responses"][username] = action + "d"
        
        approved_count = sum(1 for d in approval["responses"].values() if d == "approved")
        requires_multiple = approval["requires_multiple"]
        required_count = approval["required_count"]
        
        is_complete = False
        is_approved = False
        
        if requires_multiple:
            if approved_count >= required_count:
                is_approved = True
                is_complete = True
            elif any(d == "rejected" for d in approval["responses"].values()):
                is_complete = True
        else:
            is_complete = True
            is_approved = action == "approve"
        
        if is_complete:
            if is_approved:
                # Write status to Mini App
                write_miniapp_status(approval['title'], username, 'approved')
                
                send_to_gascity(
                    approval["conversation_id"],
                    f"APPROVAL_GRANTED: {approval['responsibility']} | {approval['title']} | By: {username}"
                )
                await query.edit_message_text(f"✅ APPROVED by {username}")
            else:
                send_to_gascity(
                    approval["conversation_id"],
                    f"APPROVAL_REJECTED: {approval['responsibility']} | {approval['title']} | By: {username}"
                )
                await query.edit_message_text(f"❌ REJECTED by {username}")
            del active_approvals[approval_id]
        else:
            await query.edit_message_text(
                f"✅ {username} {action}d\n⏳ Waiting ({approved_count}/{required_count})"
            )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    user_id = update.effective_user.id
    username = get_username_from_telegram_id(user_id)
    
    if not username:
        await update.message.reply_text("❌ Not registered")
        return
    
    conversation_id = f"telegram-{username}"
    send_to_gascity(conversation_id, f"{username}: {text}")
    await update.message.reply_text("✅ Sent to agent")

async def send_approval_request(app: Application, telegram_id: int, message_data: dict):
    """Send approval request to Telegram user"""
    try:
        msg = message_data['message']
        if msg.startswith('APPROVAL_NEEDED:'):
            parts = msg.replace('APPROVAL_NEEDED:', '').split('|')
            if len(parts) >= 3:
                responsibility = parts[0].strip()
                title = parts[1].strip()
                details = parts[2].strip()
                
                meta = router.get_responsibility_metadata(responsibility)
                requires_multiple, required_count = router.requires_multiple_approvals(responsibility)
                
                approval_id = f"{responsibility}_{datetime.now().timestamp()}"
                active_approvals[approval_id] = {
                    "responsibility": responsibility,
                    "title": title,
                    "details": details,
                    "approvers": [get_username_from_telegram_id(telegram_id)],
                    "responses": {},
                    "requires_multiple": requires_multiple,
                    "required_count": required_count,
                    "conversation_id": f"telegram-approval-{approval_id}"
                }
                
                keyboard = [[
                    InlineKeyboardButton("✅ Approve", callback_data=json.dumps({"action": "approve", "approval_id": approval_id})),
                    InlineKeyboardButton("❌ Deny", callback_data=json.dumps({"action": "reject", "approval_id": approval_id}))
                ]]
                
                text = f"{meta.get('icon', '📋')} **{title}**\n\n{details}\n\n_Type: {meta.get('name', responsibility)}_"
                
                await app.bot.send_message(
                    chat_id=telegram_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
                logger.info(f"✅ Sent approval request to {telegram_id}")
    except Exception as e:
        logger.error(f"❌ Failed to send approval: {e}")

def poll_for_messages(app: Application):
    """Poll Flask API for queued messages"""
    import asyncio
    import time
    
    logger.info("🔄 Starting message polling thread...")
    
    while True:
        try:
            if not GC_CLIENT_ID or not GC_TOKEN:
                time.sleep(5)
                continue
                
            response = requests.post(
                f"{GC_API}/v0/extmsg/outbound",
                json={"client_id": GC_CLIENT_ID, "token": GC_TOKEN},
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                messages = data.get("messages", [])
                
                if messages:
                    logger.info(f"📨 Received {len(messages)} messages from Flask API")
                    
                    for msg_data in messages:
                        # Find which Telegram users should receive this
                        msg_text = msg_data.get('message', '')
                        
                        if msg_text.startswith('APPROVAL_NEEDED:'):
                            parts = msg_text.replace('APPROVAL_NEEDED:', '').split('|')
                            if len(parts) >= 1:
                                responsibility = parts[0].strip()
                                approvers = router.get_approvers_for_responsibility(responsibility)
                                
                                for approver in approvers:
                                    telegram_id = approver['telegram_id']
                                    # Create new event loop for this thread
                                    loop = asyncio.new_event_loop()
                                    asyncio.set_event_loop(loop)
                                    try:
                                        loop.run_until_complete(
                                            send_approval_request(app, telegram_id, msg_data)
                                        )
                                    finally:
                                        loop.close()
        except Exception as e:
            logger.error(f"❌ Polling error: {e}")
        
        time.sleep(3)  # Poll every 3 seconds

def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("❌ TELEGRAM_BOT_TOKEN not set")
        return
    
    if not register_with_gascity():
        logger.error("❌ Cannot start")
        return
    
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Start message polling thread
    polling_thread = threading.Thread(target=poll_for_messages, args=(app,), daemon=True)
    polling_thread.start()
    logger.info("🔄 Message polling thread started")
    
    logger.info("🚀 Bot started!")
    app.run_polling()

if __name__ == "__main__":
    main()
