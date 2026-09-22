#!/usr/bin/env python3
"""
Quick test script to verify Telegram bot tokens work
Tests /start command without needing Gas City running
"""
import os
import asyncio
import json
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Test configuration
CONFIG_PATH = "/workspace/gascity-aws-deploy/config/responsibilities.json"

# Load config
with open(CONFIG_PATH, "r") as f:
    RESPONSIBILITIES = json.load(f)

def get_username_from_telegram_id(telegram_id: int):
    """Find username from Telegram ID"""
    for username, user_data in RESPONSIBILITIES["users"].items():
        if user_data["telegram_id"] == telegram_id:
            return username
    return None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    telegram_id = update.effective_user.id
    username = get_username_from_telegram_id(telegram_id)
    
    print(f"📨 Received /start from Telegram ID: {telegram_id}")
    
    if username:
        responsibilities = RESPONSIBILITIES["users"][username]["responsibilities"]
        resp_list = "\n".join([f"• {r}" for r in responsibilities])
        
        message = (
            f"👋 Welcome, {username}!\n\n"
            f"Your responsibilities:\n{resp_list}\n\n"
            "You'll receive approval requests here.\n\n"
            "✅ Bot connection test SUCCESSFUL!"
        )
        print(f"✅ Found user: {username}")
        print(f"📤 Sending welcome message...")
    else:
        message = (
            f"❌ Not registered.\n\n"
            f"Your Telegram ID: {telegram_id}\n\n"
            f"Add this ID to config/responsibilities.json"
        )
        print(f"⚠️  Unknown user with ID: {telegram_id}")
    
    await update.message.reply_text(message)
    print(f"✅ Message sent!")

async def test_bot(token: str, bot_name: str):
    """Test a single bot"""
    print(f"\n{'='*60}")
    print(f"🤖 Testing {bot_name}")
    print(f"{'='*60}")
    
    try:
        # Create application
        app = Application.builder().token(token).build()
        
        # Add /start handler
        app.add_handler(CommandHandler("start", start_command))
        
        print(f"✅ Bot created successfully")
        print(f"📡 Starting bot polling...")
        print(f"🔔 Waiting for /start commands...")
        print(f"   (Press Ctrl+C to stop and test next bot)")
        
        # Start polling
        await app.initialize()
        await app.start()
        await app.updater.start_polling()
        
        # Wait indefinitely
        while True:
            await asyncio.sleep(1)
            
    except KeyboardInterrupt:
        print(f"\n⏹️  Stopping {bot_name}...")
        await app.updater.stop()
        await app.stop()
        await app.shutdown()
        print(f"✅ {bot_name} stopped")
    except Exception as e:
        print(f"❌ Error testing {bot_name}: {e}")
        raise

async def main():
    """Main test function"""
    print("\n" + "="*60)
    print("🧪 TELEGRAM BOT CONNECTION TEST")
    print("="*60)
    
    # Get tokens from environment (via Doppler)
    token_you = os.getenv("TELEGRAM_BOT_TOKEN")
    token_nordice = os.getenv("TELEGRAM_BOT_TOKEN_NORDICE")
    
    if not token_you:
        print("❌ TELEGRAM_BOT_TOKEN not found in environment")
        print("   Run with: doppler run -- python3 test-bot-connection.py")
        return
    
    if not token_nordice:
        print("⚠️  TELEGRAM_BOT_TOKEN_NORDICE not found")
        print("   Will only test YOUR bot")
        token_nordice = None
    
    print(f"\n📋 Configuration loaded from: {CONFIG_PATH}")
    print(f"👥 Registered users:")
    for username, data in RESPONSIBILITIES["users"].items():
        print(f"   • {username} (ID: {data['telegram_id']})")
    
    # Test YOUR bot first
    print(f"\n{'='*60}")
    print(f"🎯 STEP 1: Testing YOUR Bot")
    print(f"{'='*60}")
    print(f"📱 Open Telegram and send /start to YOUR bot")
    
    try:
        await test_bot(token_you, "YOUR Bot")
    except KeyboardInterrupt:
        pass
    
    # Test Nordice bot if token exists
    if token_nordice:
        print(f"\n{'='*60}")
        print(f"🎯 STEP 2: Testing NORDICE Bot")
        print(f"{'='*60}")
        print(f"📱 Open Telegram and send /start to NORDICE bot")
        
        try:
            await test_bot(token_nordice, "NORDICE Bot")
        except KeyboardInterrupt:
            pass
    
    print(f"\n{'='*60}")
    print(f"✅ ALL TESTS COMPLETE!")
    print(f"{'='*60}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Test interrupted by user")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        raise
