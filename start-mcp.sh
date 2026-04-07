#!/bin/bash
# Start brain MCP server (SSE mode)
cd "$(dirname "$(readlink -f "$0")")"
[ -f .env ] && set -a && . .env && set +a

# Kill any existing instance
pkill -f "brain-mcp.py sse" 2>/dev/null
sleep 1

# Start fresh
PORT="${BRAIN_MCP_PORT:-8787}"
nohup .venv/bin/python3 brain-mcp.py sse > /tmp/brain-mcp.log 2>&1 &
echo "Brain MCP started on port $PORT (PID: $!)"
