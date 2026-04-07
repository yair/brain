# Brain — Local Setup Guide

Get the brain memory system working on your local machine.

## Prerequisites

- Python 3.11+
- Docker (for the database, or an existing Postgres 16 + pgvector instance)
- (Optional) SSH access to a remote server if the DB runs elsewhere

## Step 1: Start the Database

**Option A: Docker (recommended)**

```bash
cp .env.example .env
# Edit .env: set BRAIN_DB_PASSWORD and GEMINI_API_KEY

cd docker
docker compose up -d
```

**Option B: Remote database via SSH tunnel**

If the database runs on a remote server:

```bash
ssh -L 5432:127.0.0.1:5432 user@your-server -N &
```

Set `BRAIN_DB_HOST=127.0.0.1` in your `.env`.

**Option C: Existing Postgres instance**

Run the init scripts manually:
```bash
psql -U brain -d brain -f docker/init/001-extensions.sql
psql -U brain -d brain -f docker/init/002-schema.sql
```

## Step 2: Clone and Set Up

```bash
git clone <repo-url>
cd brain
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Step 3: Symlink CLI to PATH

```bash
mkdir -p ~/.local/bin
ln -sf "$(pwd)/brain" ~/.local/bin/brain
ln -sf "$(pwd)/brain-mcp" ~/.local/bin/brain-mcp
```

Make sure `~/.local/bin` is in your PATH. If not:
```bash
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
```

## Step 4: Test CLI

```bash
brain stats          # Should show entry counts (or zeros for fresh DB)
brain search "test"  # Should return results (or empty)
brain todos          # Should show open TODOs
```

If you get connection errors, verify the database is reachable and `.env`
is correctly configured.

## Step 5: Configure Claude Code

**Option A: CLI skill (recommended)**
```bash
ln -sf "$(pwd)/skill" ~/.claude/skills/brain
```

**Option B: MCP server**
```bash
ln -sf "$(pwd)/.mcp.json" ~/w/my-project/.mcp.json
```

## Keeping in Sync

If the brain repo is shared across machines via git:
- Pull changes to get CLI/skill updates
- The `.venv/` is gitignored — each machine creates its own
- The `.env` is gitignored — each machine has its own config

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "cannot connect to database" | Check DB is running, `.env` is correct, tunnel is up |
| "brain: command not found" | `~/.local/bin` not in PATH, or symlinks not created |
| "No module named 'psycopg2'" | Venv not set up — run `pip install -r requirements.txt` |
| MCP tools not appearing | Restart Claude Code session, check `.mcp.json` |
| "embedding failed" | Check `GEMINI_API_KEY` in `.env` |
