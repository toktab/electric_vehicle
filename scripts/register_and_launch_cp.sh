#!/bin/bash

if [ -z "$1" ]; then
    echo "Usage: ./scripts/register_and_launch_cp.sh <CP_ID> [latitude] [longitude] [price]"
    echo ""
    echo "Examples:"
    echo "  ./scripts/register_and_launch_cp.sh CP-001 40.5 -3.1 0.30"
    echo "  ./scripts/register_and_launch_cp.sh CP-010 41.0 -4.0 0.40"
    exit 1
fi

CP_ID=$1
LAT=${2:-40.5}
LON=${3:-3.1}
PRICE=${4:-0.30}

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎯 COMPLETE CP SETUP: $CP_ID"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Step 1: Register in Registry
echo "📝 Step 1: Registering in Registry..."
curl -X POST http://localhost:5001/register \
  -H "Content-Type: application/json" \
  -d "{\"cp_id\":\"$CP_ID\",\"latitude\":\"$LAT\",\"longitude\":\"$LON\",\"price_per_kwh\":$PRICE}"

echo ""
echo ""

# Step 2: Wait for Central to detect
echo "⏳ Step 2: Waiting for Central to detect CP (max 15 seconds)..."
sleep 15

# Step 3: Launch CP
echo "🚀 Step 3: Launching CP Engine and Monitor..."
./scripts/launch_cp.sh $CP_ID $LAT $LON $PRICE

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ $CP_ID is now READY FOR CHARGING!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""