# Brain MCP Setup on Zhizi

## What This Is

The brain MCP server gives Claude Code access to Zeresh's shared memory database (search, remember, todos, etc.). The DB runs on bakkies in a Docker container. This guide sets up the MCP client on zhizi so Claude Code can use it locally via stdio transport — no network-exposed MCP server needed.

## Architecture

```
zhizi                              bakkies
┌──────────────────┐               ┌──────────────────┐
│ Claude Code      │               │ brain-db (Docker) │
│   ↕ stdio        │               │ PostgreSQL:5433   │
│ brain-mcp.py     │──── TCP ────→ │ + pgvector        │
│   ↕ subprocess   │               │ + TimescaleDB     │
│ brain-cli.py     │               └──────────────────┘
└──────────────────┘
```

No HTTP server. No exposed ports on zhizi. Claude Code spawns the MCP server as a child process via stdio, which spawns the brain CLI, which connects to bakkies's postgres over the network.

## Setup Steps

### 1. Clone the repo

```bash
git clone https://github.com/yair/zeresh-brain.git
cd zeresh-brain
```

### 2. Create Python venv and install deps

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Create `.env`

```bash
cp .env.example .env
```

Edit `.env`:
```env
GEMINI_API_KEY=AIzaSyAYkIwuj_GbD1Vx63hkWT0jleiXAXJTuSQ
BRAIN_DB_HOST=<bakkies-ip-or-hostname>
BRAIN_DB_PORT=5433
BRAIN_DB_USER=brain
BRAIN_DB_PASSWORD=brain_local_only
BRAIN_DB_NAME=zeresh_brain
```

Replace `<bakkies-ip-or-hostname>` with bakkies's LAN IP or public IP/hostname. The brain-db container listens on `0.0.0.0:5433` so it's reachable if firewall allows.

**Security note:** The DB port (5433) is currently bound to `0.0.0.0` on bakkies. If zhizi is on the same LAN, use the LAN IP. If over the internet, consider an SSH tunnel instead:

```bash
# SSH tunnel option (more secure):
ssh -L 5433:127.0.0.1:5433 oc@bakkies -N &
# Then set BRAIN_DB_HOST=127.0.0.1 in .env
```

### 4. Verify the CLI works

```bash
./brain search "test" --limit 1
./brain todos
```

Both should return JSON. If connection refused, check the DB host/port/firewall.

### 5. Configure Claude Code's MCP

Copy the `.mcp.json` to your home directory (or Claude Code's config location):

```bash
# Option A: project-level (if running Claude Code from zeresh-brain dir)
# Already done — .mcp.json is in the repo

# Option B: global
cp .mcp.json ~/.mcp.json
```

Edit so the command path is absolute:

```json
{
  "mcpServers": {
    "brain": {
      "command": "/absolute/path/to/zeresh-brain/brain-mcp"
    }
  }
}
```

The `brain-mcp` shell wrapper loads `.env` and runs `brain-mcp.py` in stdio mode.

### 6. Restart Claude Code

Claude Code reads `.mcp.json` on startup. After configuring, restart it. You should see "brain" in the available MCP tools.

### 7. Test

In Claude Code, ask it to run `brain_search("test")` or `brain_todos()`. If it returns results from the DB, you're good.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "connection refused" | Check BRAIN_DB_HOST, port 5433 reachable from zhizi |
| "authentication failed" | Check BRAIN_DB_USER/PASSWORD match bakkies's pg_hba.conf |
| "brain command not found" | Use absolute path in .mcp.json, or add to PATH |
| MCP tools not showing | Restart Claude Code, check ~/.mcp.json syntax |
| Embeddings fail | Check GEMINI_API_KEY in .env |

## What NOT to Do

- Don't run `start-mcp.sh` on zhizi — that's for SSE mode (bakkies only, legacy)
- Don't expose port 8787 — stdio mode doesn't need any network listener
- Don't edit `brain-cli.py` on zhizi — pull changes from git
