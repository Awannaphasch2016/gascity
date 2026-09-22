#!/usr/bin/env python3
"""
Fixed Test Approval Sender
Works with actual bot registrations
"""

import requests
import time
import sys
import json

API_URL = "http://13.214.162.41:7375"

def get_registered_bots():
    """Get list of registered bot clients"""
    try:
        response = requests.post(f"{API_URL}/v0/extmsg/clients", json={})
        if response.status_code == 200:
            client = response.json()
            print(f"✅ Found registered client: {client['client_id'][:8]}...")
            return [client]
        return []
    except Exception as e:
        print(f"❌ Error getting clients: {e}")
        return []

def send_to_all_bots(message):
    """Send message to all registered bots by broadcasting"""
    # Try sending with a generic client_id
    # The Flask app should forward to all connected bots
    data = {
        "client_id": "test-sender",
        "message": message
    }
    
    try:
        response = requests.post(f"{API_URL}/v0/extmsg/inbound", json=data, timeout=10)
        return response
    except Exception as e:
        print(f"❌ Error: {e}")
        return None

def main():
    print("╔═══════════════════════════════════════════════════╗")
    print("║       🏗️  Test Approval System                   ║")  
    print("╚═══════════════════════════════════════════════════╝")
    print()
    
    # Get registered bots
    print("🔍 Checking registered bots...")
    bots = get_registered_bots()
    print()
    
    if not bots:
        print("⚠️  No bots found. Make sure the Telegram bots are running.")
        print()
    
    approvals = [
        {
            "name": "Deployment Approval",
            "badge": "👥 BOTH APPROVERS",
            "message": "APPROVAL_NEEDED: deployment_approval | Deploy to Production | Landing page builder v2.0 ready to deploy"
        },
        {
            "name": "Security Review",
            "badge": "🔒 KARANT ONLY",
            "message": "APPROVAL_NEEDED: security_review | Security Audit | Review OAuth2 implementation in PR #123"
        },
        {
            "name": "Budget Approval",
            "badge": "💰 YOU ONLY",
            "message": "APPROVAL_NEEDED: budget_approval | Infrastructure Upgrade | $200/month for EC2 instances"
        }
    ]
    
    print("Sending test approvals...")
    print()
    
    for approval in approvals:
        print(f"📤 {approval['name']}")
        print(f"   {approval['badge']}")
        
        response = send_to_all_bots(approval['message'])
        
        if response and response.status_code == 200:
            print(f"   ✅ Sent!")
        elif response:
            print(f"   ⚠️  HTTP {response.status_code}")
            print(f"   {response.text[:200]}")
        else:
            print(f"   ❌ Failed to send")
        
        print()
        time.sleep(1)
    
    print("✅ Test complete!")
    print("📱 Check your Telegram bots for approval requests")
    print("📊 View Mini App: http://13.214.162.41:8080")

if __name__ == "__main__":
    main()
