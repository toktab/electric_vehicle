#!/bin/bash

if [ -z "$1" ]; then
    echo "Usage: ./scripts/stop_cp.sh <CP_ID>"
    echo "Example: ./scripts/stop_cp.sh CP-003"
    exit 1
fi

CP_ID=$1
CP_NUM=$(echo $CP_ID | sed 's/CP-//' | sed 's/^0*//')

echo "🛑 Stopping $CP_ID..."

# Stop containers
docker stop "evcharging_cp_engine_${CP_NUM}" 2>/dev/null
docker stop "evcharging_cp_monitor_${CP_NUM}" 2>/dev/null

# Remove containers
docker rm "evcharging_cp_engine_${CP_NUM}" 2>/dev/null
docker rm "evcharging_cp_monitor_${CP_NUM}" 2>/dev/null

echo "✅ $CP_ID stopped and removed."
echo ""
echo "To unregister from Registry:"
echo "  curl -X DELETE http://localhost:5001/unregister/$CP_ID"