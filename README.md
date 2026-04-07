# Brain

A persistent, structured memory layer for AI agents. Brain gives every
AI session — Claude Code, custom agents, cron jobs — shared
read/write access to a single Postgres database with vector embeddings,
full-text search, and time-decay ranking.

Any session can search what another session decided, stored, or learned.
Decisions survive session boundaries. TODOs stay current. Context is
never lost.

## Architecture

```
                     brain
               (Postgres + pgvector)
                       |
          CLI / MCP / Claude Code skill
                       |
        +-------+------+------+---------+
        |       |             |         |
    Claude   Custom       Briefing   Triage
    Code     agents       crons      bots
```

Three access methods, all equivalent:

| Method | Best for | How |
|--------|----------|-----|
| **CLI** (`brain`) | Scripts, Claude Code, any shell | `brain --json search "query"` |
| **MCP server** (`brain-mcp`) | MCP-compatible clients | stdio or SSE transport |
| **Claude Code skill** (`/brain`) | Claude Code sessions | Install `skill/` globally |

The CLI is recommended for Claude Code — it's stateless (no connection to
drop), trivially resilient, and works everywhere.

## Quick start

### 1. Start the database

```bash
cp .env.example .env
# Edit .env: set BRAIN_DB_PASSWORD and GEMINI_API_KEY

cd docker
docker compose up -d
```

This starts Postgres 16 with pgvector and TimescaleDB, initializes the
schema, and binds to localhost.

### 2. Install the CLI

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

mkdir -p ~/.local/bin
ln -sf "$(pwd)/brain" ~/.local/bin/brain
```

### 3. Verify

```bash
brain stats
brain remember --kind fact --title "Test entry" --body "Brain is working"
brain search "test"
brain forget <id-from-above>
```

### 4. (Optional) Claude Code skill

```bash
ln -sf "$(pwd)/skill" ~/.claude/skills/brain
```

The skill teaches Claude Code to use the brain CLI automatically — when
to search, when to store, how to boost results.

### 5. (Optional) MCP server

For MCP-compatible clients that don't support CLI tools:

```bash
# stdio mode (Claude Code, Cursor, etc.)
ln -sf "$(pwd)/brain-mcp" ~/.local/bin/brain-mcp
```

Add to your project's `.mcp.json`:
```json
{ "mcpServers": { "brain": { "command": "brain-mcp" } } }
```

## Data model

### entries — the core table

Every piece of knowledge is an entry with a kind:

| Kind | Purpose |
|------|---------|
| `decision` | A choice made during work, with reasoning |
| `fact` | Something true and useful |
| `todo` | A persistent task (status: active/done/blocked) |
| `insight` | A realization or pattern |
| `observation` | Something seen or reported |
| `preference` | How someone likes things done |
| `debrief` | Post-session or post-incident summary |

Entries have: title, body, tags, project, entity_refs, source,
confidence, embedding (768-dim vector), full-text search vector,
and lifecycle fields (superseded_by, expires_at, status).

### entities — people, projects, tools

Keyed by slug (e.g., `alice`, `my-project`, `postgres`). Stores
name, kind, and arbitrary metadata as JSONB.

### events — calendar

Title, start/end times, location, attendees, notes.

### location — presence tracking

GPS coordinates with source, accuracy, and label.

## CLI reference

### Reading

```bash
brain search "query" [--kind K] [--project P] [--since S] [--limit N]
brain recent [--kind K] [--status S] [--project P] [--since S] [--limit N]
brain get <entry-id>
brain context <project>        # decisions + TODOs + entities + recent
brain todos [--project P]
brain entities
brain entity <slug>
brain events [--from D] [--to D]
brain where                    # latest known location
brain stats
```

### Writing

```bash
brain remember --kind K --title T --body B [--source S] [--project P] [--tags T] [--entity-refs E]
brain update <id> [--status S] [--body B] [--title T] [--confidence C]
brain supersede <old-id> --title T --body B
brain forget <id>
brain boost [--retrieval R] <ids-or-positions...> [--source S] [--context C]
```

### Global flags

| Flag | Effect |
|------|--------|
| `--json` | Structured JSON output (use from AI agents) |
| `--quiet` | Minimal output (IDs/titles only) |
| `--db` | Database name (default from `BRAIN_DB_NAME` env var) |

## Search ranking

`brain search` uses hybrid ranking:
- **70% semantic similarity** via Gemini embeddings (pgvector cosine distance)
- **30% keyword match** via Postgres full-text search
- **+5% boost score** from access_log (time-decayed over 30 days)

Embeddings are generated on write via the Gemini API. Each search records
a retrieval ID for positional boosting.

## Remote access

If the database runs on a remote server, use an SSH tunnel:

```bash
ssh -L 5432:127.0.0.1:5432 user@your-server -N &
```

Then set `BRAIN_DB_HOST=127.0.0.1` in `.env`. The CLI connects to
whatever host/port is configured — local or tunneled.

## Configuration

All configuration is via environment variables (loaded from `.env`
by the wrapper scripts):

| Variable | Default | Purpose |
|----------|---------|---------|
| `BRAIN_DB_HOST` | 127.0.0.1 | Database host |
| `BRAIN_DB_PORT` | 5432 | Database port |
| `BRAIN_DB_USER` | brain | Database user |
| `BRAIN_DB_PASSWORD` | (none) | Database password |
| `BRAIN_DB_NAME` | brain | Database name |
| `GEMINI_API_KEY` | (none) | For embedding generation |
| `BRAIN_MCP_PORT` | 8787 | MCP server port (SSE mode) |

## Files

| File | Purpose |
|------|---------|
| `brain` | Shell wrapper — activates venv, runs CLI |
| `brain-cli.py` | CLI implementation (click-based) |
| `brain-mcp` | Shell wrapper for MCP server |
| `brain-mcp.py` | MCP server (stdio/SSE transport) |
| `skill/` | Claude Code skill (symlink to `~/.claude/skills/brain`) |
| `docker/` | Docker Compose + schema init scripts |
| `requirements.txt` | Python dependencies |
| `docs/` | Design documents and setup guides |

## License

MIT
