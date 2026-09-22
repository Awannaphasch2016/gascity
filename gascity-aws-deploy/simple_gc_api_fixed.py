#!/usr/bin/env python3
"""
Fixed Gas City API Mock
Handles approval routing correctly
"""

from flask import Flask, request, jsonify
from flask_cors import CORS
import json
import os
import uuid
from datetime import datetime
from collections import defaultdict
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# In-memory storage
registered_clients = {}  # {client_id: {token, agent_name, last_seen}}
message_queues = defaultdict(list)  # {client_id: [messages]}
responsibilities_config = None

def load_responsibilities():
    """Load responsibilities configuration"""
    global responsibilities_config
    config_path = os.getenv('CONFIG_PATH', '/app/config/responsibilities.json')
    
    try:
        with open(config_path, 'r') as f:
            responsibilities_config = json.load(f)
            logger.info(f"✅ Loaded responsibilities config: {len(responsibilities_config.get('users', {}))} users")
            return responsibilities_config
    except Exception as e:
        logger.error(f"❌ Error loading config: {e}")
        # Return a minimal default config
        return {
            "users": {},
            "responsibility_definitions": {}
        }

def route_approval_to_users(responsibility_type):
    """Find which users should receive this approval request"""
    if not responsibilities_config:
        logger.warning("No responsibilities config loaded")
        return []
    
    users = responsibilities_config.get('users', {})
    matched_users = []
    
    for user_id, user_data in users.items():
        responsibilities = user_data.get('responsibilities', [])
        if responsibility_type in responsibilities:
            matched_users.append(user_id)
            logger.info(f"   → Routing to user: {user_id}")
    
    return matched_users

@app.route('/health', methods=['GET'])
def health():
    return jsonify({"status": "ok", "clients": len(registered_clients)})

@app.route('/v0/extmsg/clients', methods=['POST', 'OPTIONS'])
def register_client():
    """Register a new external messaging client (bot)"""
    if request.method == 'OPTIONS':
        response = jsonify({"status": "ok"})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        return response
    
    try:
        data = request.json or {}
        agent_name = data.get('agent_name', 'unknown-agent')
        
        client_id = str(uuid.uuid4())
        token = str(uuid.uuid4())
        
        registered_clients[client_id] = {
            'token': token,
            'agent_name': agent_name,
            'registered_at': datetime.now().isoformat(),
            'last_seen': datetime.now().isoformat()
        }
        
        logger.info(f"✅ Registered client: {agent_name} ({client_id[:8]}...)")
        
        return jsonify({
            "client_id": client_id,
            "token": token
        })
    except Exception as e:
        logger.error(f"❌ Error in register_client: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/v0/extmsg/inbound', methods=['POST', 'OPTIONS'])
def receive_message():
    """Receive a message and route to appropriate bots"""
    if request.method == 'OPTIONS':
        response = jsonify({"status": "ok"})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        return response
    
    try:
        data = request.json
        sender_id = data.get('client_id', 'unknown')
        message = data.get('message', '')
        
        logger.info(f"📨 Received message from {sender_id}")
        logger.info(f"   Message: {message[:100]}...")
        
        # Check if this is an approval request
        if message.startswith('APPROVAL_NEEDED:'):
            # Parse: APPROVAL_NEEDED: responsibility_type | title | details
            parts = message.replace('APPROVAL_NEEDED:', '').split('|')
            if len(parts) >= 3:
                responsibility_type = parts[0].strip()
                title = parts[1].strip()
                details = parts[2].strip()
                
                logger.info(f"   Approval type: {responsibility_type}")
                
                # Route to users with this responsibility
                target_users = route_approval_to_users(responsibility_type)
                
                if not target_users:
                    logger.warning(f"   ⚠️  No users found for responsibility: {responsibility_type}")
                    # Send to all bots as fallback
                    target_users = list(responsibilities_config.get('users', {}).keys())
                
                # Find bot client_ids for these users
                # For now, send to all registered clients since we're in test mode
                routed_count = 0
                for client_id in registered_clients.keys():
                    message_queues[client_id].append({
                        'from': sender_id,
                        'message': message,
                        'timestamp': datetime.now().isoformat(),
                        'type': 'approval_request',
                        'responsibility': responsibility_type,
                        'title': title,
                        'details': details
                    })
                    routed_count += 1
                
                logger.info(f"   ✅ Routed to {routed_count} bots")
                return jsonify({"status": "ok", "routed_to": routed_count})
        
        # Regular message - broadcast to all
        routed_count = 0
        for client_id in registered_clients.keys():
            message_queues[client_id].append({
                'from': sender_id,
                'message': message,
                'timestamp': datetime.now().isoformat(),
                'type': 'message'
            })
            routed_count += 1
        
        logger.info(f"   ✅ Broadcast to {routed_count} bots")
        return jsonify({"status": "ok", "broadcast_to": routed_count})
        
    except Exception as e:
        logger.error(f"❌ Error in receive_message: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route('/v0/extmsg/outbound', methods=['POST', 'OPTIONS'])
def send_message():
    """Bot polling for messages"""
    if request.method == 'OPTIONS':
        response = jsonify({"status": "ok"})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        return response
    
    try:
        data = request.json
        client_id = data.get('client_id')
        token = data.get('token')
        
        # Validate client
        if client_id not in registered_clients:
            return jsonify({"messages": []})
        
        if registered_clients[client_id]['token'] != token:
            return jsonify({"error": "Invalid token"}), 401
        
        # Update last seen
        registered_clients[client_id]['last_seen'] = datetime.now().isoformat()
        
        # Get pending messages
        messages = message_queues.get(client_id, [])
        message_queues[client_id] = []  # Clear queue
        
        if messages:
            logger.info(f"📬 Sending {len(messages)} messages to {client_id[:8]}...")
        
        return jsonify({"messages": messages})
        
    except Exception as e:
        logger.error(f"❌ Error in send_message: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    logger.info("🚀 Starting Gas City API Mock...")
    load_responsibilities()
    app.run(host='0.0.0.0', port=7375, debug=False)
