#!/usr/bin/env python3
"""
Test Approval Sender
Sends test approval requests to the deployed Telegram bots
"""

import requests
import time
import sys

# Your EC2 instance IP
API_URL = "http://13.214.162.41:7375/v0/extmsg/inbound"

# Test approval requests
APPROVALS = [
    {
        "name": "Deployment Approval",
        "badge": "👥 BOTH APPROVERS",
        "data": {
            "client_id": "deploy-agent",
            "message": "APPROVAL_NEEDED: deployment_approval | Deploy to Production | New feature: Landing page builder v2.0 - includes hero section, feature cards, and testimonials. Ready to deploy?"
        }
    },
    {
        "name": "Security Review", 
        "badge": "🔒 KARANT ONLY",
        "data": {
            "client_id": "deploy-agent",
            "message": "APPROVAL_NEEDED: security_review | Security Audit | Please review authentication flow changes in PR #123. New OAuth2 implementation with refresh tokens."
        }
    },
    {
        "name": "Budget Approval",
        "badge": "💰 YOU ONLY", 
        "data": {
            "client_id": "deploy-agent",
            "message": "APPROVAL_NEEDED: budget_approval | Infrastructure Upgrade | Requesting $200/month for additional EC2 instances to handle increased traffic."
        }
    }
]

def print_banner():
    print("╔═══════════════════════════════════════════════════╗")
    print("║       🏗️  Test Approval System                   ║")
    print("║       Send approval requests to Telegram bots     ║")
    print("╚═══════════════════════════════════════════════════╝")
    print()

def send_approval(approval):
    """Send a single approval request"""
    print(f"📤 Sending: {approval['name']}")
    print(f"   {approval['badge']}")
    
    try:
        response = requests.post(API_URL, json=approval['data'], timeout=10)
        
        if response.status_code == 200:
            print(f"   ✅ Sent successfully!")
        else:
            print(f"   ⚠️  HTTP {response.status_code}: {response.text}")
    except requests.exceptions.ConnectionError:
        print(f"   ❌ Connection failed. Is the Flask API running on port 7375?")
    except requests.exceptions.Timeout:
        print(f"   ❌ Request timed out")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    print()

def main():
    print_banner()
    
    if len(sys.argv) > 1:
        # Send specific approval by index
        try:
            index = int(sys.argv[1]) - 1
            if 0 <= index < len(APPROVALS):
                send_approval(APPROVALS[index])
            else:
                print(f"❌ Invalid index. Choose 1-{len(APPROVALS)}")
                print_menu()
        except ValueError:
            print("❌ Invalid argument. Provide a number 1-3")
            print_menu()
    else:
        # Interactive menu
        print_menu()
        print("Sending all approvals in 3 seconds...")
        print("(Press Ctrl+C to cancel)")
        print()
        
        try:
            time.sleep(3)
            for approval in APPROVALS:
                send_approval(approval)
                time.sleep(1)  # Small delay between requests
            
            print("✅ All test approvals sent!")
            print("📱 Check your Telegram bots for approval requests")
            print(f"📊 View Mini App: http://13.214.162.41:8080")
            
        except KeyboardInterrupt:
            print("\n❌ Cancelled by user")
            sys.exit(0)

def print_menu():
    print("Available test approvals:")
    for i, approval in enumerate(APPROVALS, 1):
        print(f"  {i}. {approval['name']} ({approval['badge']})")
    print()
    print("Usage:")
    print("  python3 send-test-approvals.py        # Send all")
    print("  python3 send-test-approvals.py 1      # Send deployment only")
    print("  python3 send-test-approvals.py 2      # Send security only")  
    print("  python3 send-test-approvals.py 3      # Send budget only")
    print()

if __name__ == "__main__":
    main()
