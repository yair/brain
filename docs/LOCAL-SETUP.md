# Brain MCP — Local Setup Guide

Get the brain memory system working with Claude Code on your local machine (zhizi, laptop, etc.).

## Prerequisites

- Claude Code installed (`claude` CLI)
- Python 3.11+
- SSH access to bakkies (albanialink.com)

## Step 1: SSH Tunnel to Brain DB

The brain database runs on bakkies at port 5433. You need a tunnel.

**On zhizi**, this is already handled — zeresh's OpenClaw SSH tunnel service
(`openclaw-tunnel-zeresh.service`) forwards port 5433 alongside the OpenClaw
gateway port. No extra setup needed.

**On other machines**, add to your SSH config (`~/.ssh/config`):
```
Host bakkies
    HostName albanialink.com
    User jay  # or your user
    LocalForward 5433 127.0.0.1:5433
```

Or run manually:
```bash
ssh -L 5433:127.0.0.1:5433 jay@albanialink.com -N &
```

Verify the tunnel:
```bash
PGPASSWORD=brain_local_only psql -h 127.0.0.1 -p 5433 -U brain -d zeresh_brain -c "SELECT count(*) FROM entries;"
```

## Step 2: Clone the Repo

```bash
git clone <repo-url> ~/w/zeresh-brain   # or wherever you keep it
cd ~/w/zeresh-brain
```

## Step 3: Create Venv and Install Dependencies

Modern Python (3.12+) on Debian refuses system-wide pip installs (PEP 668).
Use a venv inside the repo:

```bash
python3 -m venv .venv
.venv/bin/pip install psycopg2-binary click requests mcp
```

The `.venv/` directory is gitignored — each machine creates its own.

## Step 4: Symlink the Wrapper Scripts

The repo includes `brain` and `brain-mcp` shell wrappers that automatically
find and use the co-located `.venv`. Symlink them into your PATH:

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/brain" ~/.local/bin/brain
ln -sf "$(pwd)/brain-mcp" ~/.local/bin/brain-mcp
```

Make sure `~/.local/bin` is in your PATH. If not, add to `~/.bashrc`:
```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Step 5: Test CLI

```bash
brain stats          # Should show entry counts
brain search "test"  # Should return results
brain todos          # Should show open TODOs
```

If you get connection errors, check the SSH tunnel is running.

## Step 6: Configure Claude Code MCP

The repo includes a portable `.mcp.json` that just calls `brain-mcp` (found
via PATH). Symlink it into any project where you want brain access:

```bash
ln -sf ~/w/zeresh-brain/.mcp.json ~/w/my-project/.mcp.json
```

Then restart your Claude Code session. Verify by asking:
```
> Use brain_search to find entries about "handwave"
```

## Keeping in Sync

The Python scripts are the same on bakkies and locally — they connect to the
same DB via the tunnel. Keep them in sync via:
- **Git** (recommended): push from bakkies, pull locally
- **Syncthing**: sync the repo directory
- **scp**: manual copy when needed

## Troubleshooting

**"cannot connect to database"**
→ SSH tunnel not running. Check that port 5433 is forwarded.

**"brain: command not found"**
→ `~/.local/bin` not in PATH, or symlinks not created. See Step 4.

**"No module named 'psycopg2'" or similar import errors**
→ Venv not set up. See Step 3. The wrapper scripts use `.venv/` in the repo
  root — make sure it exists and has the deps installed.

**MCP tools not appearing in Code**
→ Check `.mcp.json` symlink exists in your project root. Restart Code session.

**"embedding failed"**
→ Gemini API key is hardcoded in brain-cli.py. Works from anywhere with internet.

## Architecture

```
Your Machine                          Bakkies (albanialink.com)
┌──────────────────┐                  ┌──────────────────────┐
│ Claude Code      │                  │                      │
│   ↕ MCP/stdio    │                  │  brain-db container  │
│ brain-mcp        │                  │  (Postgres+pgvector) │
│  (wrapper script)│                  │  port 5433           │
│   ↕ .venv/python │   SSH tunnel     │                      │
│ brain-mcp.py     │  localhost:5433   │                      │
│   ↕ subprocess   ├──────────────────┼──►                   │
│ brain-cli.py     │                  │                      │
└──────────────────┘                  └──────────────────────┘
```

All data lives in the DB on bakkies. Local setup is just:
venv + wrapper scripts + symlinks + tunnel.
