#!/bin/bash

if [ -z "$1" ]; then
    echo "Usage: ./scripts/launch_cp.sh <CP_ID> [latitude] [longitude] [price]"
    echo ""
    echo "Examples:"
    echo "  ./scripts/launch_cp.sh CP-001 40.5 -3.1 0.30"
    echo "  ./scripts/launch_cp.sh CP-010 41.0 -4.0 0.40"
    exit 1
fi

CP_ID=$1
LAT=${2:-40.5}
LON=${3:-3.1}
PRICE=${4:-0.30}

# Extract number from CP_ID (e.g., CP-001 -> 1)
CP_NUM=$(echo $CP_ID | sed 's/CP-//' | sed 's/^0*//')
CP_PORT=$((6000 + CP_NUM))

NETWORK="evcharging_net"
IMAGE="evcharging-cp:latest"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🚀 Launching Charging Point: $CP_ID"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "📍 Location: ($LAT, $LON)"
echo "💰 Price: €${PRICE}/kWh"
echo "🔌 Port: $CP_PORT"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check if network exists
if ! docker network inspect $NETWORK >/dev/null 2>&1; then
    echo "❌ Network '$NETWORK' not found!"
    echo "   Please start the system first: docker-compose up -d"
    exit 1
fi

# Build CP image if it doesn't exist
if ! docker image inspect $IMAGE >/dev/null 2>&1; then
    echo "📦 Building CP image..."
    docker build -t $IMAGE -f Dockerfile.cp .
fi

# Launch Engine
echo "🔧 Starting CP Engine..."
docker run -d \
    --name "evcharging_cp_engine_${CP_NUM}" \
    --network $NETWORK \
    -p ${CP_PORT}:${CP_PORT} \
    -e KAFKA_BROKER=kafka:9092 \
    -it \
    $IMAGE \
    python charging_point/ev_cp_engine.py $CP_ID $LAT $LON $PRICE central 5000

# Wait a bit
sleep 2

# Launch Monitor
echo "🔍 Starting CP Monitor..."
docker run -d \
    --name "evcharging_cp_monitor_${CP_NUM}" \
    --network $NETWORK \
    -e KAFKA_BROKER=kafka:9092 \
    -it \
    $IMAGE \
    python charging_point/ev_cp_monitor.py $CP_ID evcharging_cp_engine_${CP_NUM} ${CP_PORT} central 5000

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ $CP_ID launched successfully!"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "📊 Check status:"
echo "   docker logs evcharging_cp_engine_${CP_NUM}"
echo "   docker logs evcharging_cp_monitor_${CP_NUM}"
echo ""
echo "🔗 Attach to engine:"
echo "   docker attach evcharging_cp_engine_${CP_NUM}"
echo ""