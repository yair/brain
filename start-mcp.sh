#!/bin/bash
# Start brain MCP server (SSE on port 8787)
cd "$(dirname "$0")"
source .env
export PATH="/home/oc/.local/bin:$PATH"

# Kill any existing instance
pkill -f "brain-mcp.py sse" 2>/dev/null
sleep 1

# Start fresh
nohup .venv/bin/python3 brain-mcp.py sse > /tmp/brain-mcp.log 2>&1 &
echo "Brain MCP started on port 8787 (PID: $!)"
