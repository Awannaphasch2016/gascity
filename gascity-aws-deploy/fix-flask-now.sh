#!/bin/bash
# One-Command Fix for Flask API
# Run this on your EC2 instance: bash <(curl -s https://raw.githubusercontent.com/Awannaphasch2016/gascity/main/gascity-aws-deploy/fix-flask-now.sh)

set -e

echo "╔══════════════════════════════════════════════════════════╗"
echo "║          🔧 Fixing Flask API Now...                      ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Navigate to app directory
cd /opt/gascity

echo "📥 Downloading fixed Flask app..."
wget -q -O simple_gc_api_new.py https://raw.githubusercontent.com/Awannaphasch2016/gascity/main/gascity-aws-deploy/simple_gc_api_fixed.py

echo "🛑 Stopping old Flask app..."
pkill -f simple_gc_api.py || echo "   (was not running)"

echo "💾 Backing up old version..."
mv -f simple_gc_api.py simple_gc_api.py.bak 2>/dev/null || true

echo "✨ Installing fixed version..."
mv -f simple_gc_api_new.py simple_gc_api.py
chmod +x simple_gc_api.py

echo "🚀 Starting fixed Flask app..."
nohup python3 simple_gc_api.py > flask.log 2>&1 &
sleep 3

echo ""
echo "✅ Testing Flask app..."
HEALTH=$(curl -s http://localhost:7375/health)
echo "   Health check: $HEALTH"

if echo "$HEALTH" | grep -q '"status":"ok"'; then
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║          ✅ Flask API Fixed Successfully!                ║"
    echo "╚══════════════════════════════════════════════════════════╝"
    echo ""
    echo "🎉 You can now send test approvals:"
    echo "   python3 send-test-approvals.py"
    echo ""
    echo "📊 Or access from anywhere:"
    echo "   http://13.214.162.41:8080"
    echo ""
else
    echo ""
    echo "❌ Something went wrong. Check logs:"
    echo "   tail -f /opt/gascity/flask.log"
fi
