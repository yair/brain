# Brain MCP — Local Setup Guide

Get the brain memory system working with Claude Code on your local machine (zhizi, laptop, etc.).

## Prerequisites

- Claude Code installed (`claude` CLI)
- Python 3.11+
- SSH access to bakkies (albanialink.com)

## Step 1: SSH Tunnel to Brain DB

The brain database runs on bakkies at port 5433. Tunnel it:

```bash
# Add to your SSH config (~/.ssh/config):
Host bakkies
    HostName albanialink.com
    User jay  # or your user
    LocalForward 5433 127.0.0.1:5433

# Or run manually:
ssh -L 5433:127.0.0.1:5433 jay@albanialink.com -N &
```

Verify the tunnel:
```bash
PGPASSWORD=brain_local_only psql -h 127.0.0.1 -p 5433 -U brain -d zeresh_brain -c "SELECT count(*) FROM entries;"
```

## Step 2: Install Dependencies

```bash
pip3 install psycopg2-binary click requests mcp
```

## Step 3: Copy Brain Files

You need two files from bakkies:

```bash
# From bakkies:
scp bakkies:/home/oc/projects/zeresh-brain/brain-cli.py ~/projects/zeresh-brain/
scp bakkies:/home/oc/projects/zeresh-brain/brain-mcp.py ~/projects/zeresh-brain/

# Make CLI executable and symlink
chmod +x ~/projects/zeresh-brain/brain-cli.py
mkdir -p ~/.local/bin
ln -sf ~/projects/zeresh-brain/brain-cli.py ~/.local/bin/brain
```

Or use Syncthing — if `~/projects/zeresh-brain/` is synced, you're already done.

## Step 4: Test CLI

```bash
brain stats          # Should show entry counts
brain search "test"  # Should return results
brain todos          # Should show open TODOs
```

If you get connection errors, check the SSH tunnel is running.

## Step 5: Configure Claude Code MCP

Create `~/.mcp.json` (applies globally) or `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "brain": {
      "command": "python3",
      "args": ["/absolute/path/to/zeresh-brain/brain-mcp.py"]
    }
  }
}
```

**Adjust the path** to wherever you put `brain-mcp.py`.

## Step 6: Verify in Claude Code

Start a Code session and ask it to use the brain:

```
> Use brain_search to find entries about "handwave"
```

You should see Code call the `brain_search` MCP tool and return results.

## Keeping in Sync

The brain-cli.py and brain-mcp.py files are the same on bakkies and locally — they connect to the same DB via the tunnel. Updates to the CLI on bakkies should be synced to local copies.

Options:
- **Syncthing** (recommended): sync the `~/projects/zeresh-brain/` directory
- **Git**: push from bakkies, pull locally
- **scp**: manual copy when needed

## Troubleshooting

**"cannot connect to database"**
→ SSH tunnel not running. Check `ssh -L 5433:...` is alive.

**"brain: command not found"**
→ `~/.local/bin` not in PATH. Add to `~/.bashrc`: `export PATH="$HOME/.local/bin:$PATH"`

**MCP tools not appearing in Code**
→ Check `.mcp.json` path is correct and absolute. Restart Code session.

**"embedding failed"**
→ Gemini API key is hardcoded in brain-cli.py. Works from anywhere with internet access.

## Architecture

```
Your Machine                          Bakkies (albanialink.com)
┌──────────────────┐                  ┌──────────────────────┐
│ Claude Code      │                  │                      │
│   ↕ MCP/stdio    │                  │  brain-db container  │
│ brain-mcp.py     │                  │  (Postgres+pgvector) │
│   ↕ subprocess   │   SSH tunnel     │  port 5433           │
│ brain-cli.py  ───┼──────────────────┼──►                   │
│   (brain CLI)    │  localhost:5433   │                      │
└──────────────────┘                  └──────────────────────┘
```

All data lives in the DB on bakkies. Local setup is just the CLI + MCP wrapper + tunnel.
