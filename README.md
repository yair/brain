# Zeresh Brain

A shared, structured memory layer for Jay's AI ecosystem. Every AI session
(Claude Code, OpenClaw agents, briefings, triage) reads and writes to one
Postgres database with vector embeddings, so decisions, facts, and context
survive across sessions and projects.

## Why

Without a shared brain, each new AI session starts nearly blind. Decisions
made in one project are invisible to another. TODOs drift. Context is lost.
The brain fixes this: a single queryable database that any session can search,
write to, and learn from.

## Architecture

```
                      zeresh-brain
                  (Postgres 16 + pgvector)
                  hosted on bakkies (albanialink.com)
                          |
               SSH tunnel (port 5433)
                          |
        +---------+-------+-------+-----------+
        |         |               |           |
    Claude     OpenClaw        Briefing    Triage
    Code       agents          crons       (junior)
   (brain      (brain          (brain      (brain
    CLI)        CLI)            CLI)        CLI)
```

All access goes through the `brain` CLI. No MCP server needed — each
invocation connects directly to the DB through the SSH tunnel, does its
work, and exits. Stateless, resilient, zero infrastructure.

## Tables

| Table      | Purpose                                          |
|------------|--------------------------------------------------|
| `entries`  | Decisions, facts, TODOs, insights, observations, preferences, debriefs |
| `entities` | People, projects, tools, clients (keyed by slug) |
| `events`   | Calendar events, meetings, deadlines             |
| `location` | GPS/presence data (Jay's location)               |
| `access_log` | Boost/citation tracking for search ranking     |
| `retrievals` | Search result sets (for positional boosting)   |

### entries schema

| Column         | Type          | Notes                                      |
|----------------|---------------|--------------------------------------------|
| `id`           | UUID          | Primary key                                |
| `kind`         | text          | decision, fact, todo, insight, observation, preference, debrief |
| `source`       | text          | Who wrote it: claude-code, zeresh, cli, junior, ... |
| `title`        | text          | Short, searchable summary                  |
| `body`         | text          | Full content                               |
| `tags`         | text[]        | Freeform tags for filtering                |
| `project`      | text          | Project slug (handwave, zhizi, abelard, ...) |
| `entity_refs`  | text[]        | Referenced entity slugs                    |
| `embedding`    | vector(768)   | Gemini embedding for semantic search       |
| `tsv`          | tsvector      | Full-text search vector (auto-generated)   |
| `status`       | text          | active, done, blocked, ...                 |
| `confidence`   | float         | 0-1, can be updated over time              |
| `superseded_by`| UUID          | Points to replacement entry                |
| `expires_at`   | timestamptz   | Soft-delete / TTL                          |

## Installation

### Prerequisites

- Python 3.11+
- SSH tunnel to bakkies forwarding port 5433 (brain DB)
- Gemini API key (for embeddings, optional but recommended)

### Setup

```bash
cd ~/w/zeresh-brain
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Create wrapper symlinks
ln -sf "$(pwd)/brain" ~/.local/bin/brain
ln -sf "$(pwd)/brain-mcp" ~/.local/bin/brain-mcp  # optional, for MCP server mode

# Set up env (in ~/.local/bin/.env or export directly)
export GEMINI_API_KEY="your-key-here"
```

### Verify

```bash
brain stats           # should show entry counts
brain recent          # should list recent entries
brain search "test"   # should return results
```

### Claude Code integration

Install the brain skill globally:

```bash
mkdir -p ~/.claude/skills/brain
# Copy or symlink SKILL.md from this repo
cp skill/SKILL.md ~/.claude/skills/brain/SKILL.md
```

Claude Code sessions can then use `/brain` or call `brain` via Bash
directly. No MCP server, no `.mcp.json`, no dropped connections.

## CLI Reference

### Global flags

| Flag      | Effect                           |
|-----------|----------------------------------|
| `--json`  | JSON output (use from AI agents) |
| `--quiet` | Minimal output (IDs/titles only) |
| `--db`    | Database name (default: zeresh_brain) |

### Commands

**Search & Read:**

```bash
brain search "query" [--kind K] [--project P] [--since S] [--limit N]
brain recent [--kind K] [--status S] [--project P] [--since S] [--limit N]
brain get <entry-id>
brain context <project>        # decisions + TODOs + entities + recent
brain todos [--project P]
brain entities
brain entity <slug>
brain events [--from D] [--to D]
brain where-is-jay
brain stats
brain boost-history [entry-id] [--limit N]
```

**Write:**

```bash
brain remember --kind K --title T --body B [--source S] [--project P] [--tags T] [--entity-refs E] [--status S] [--confidence C]
brain update <entry-id> [--status S] [--body B] [--title T] [--confidence C]
brain supersede <old-id> --title T --body B [--source S]
brain forget <entry-id>
brain boost [--retrieval R] <ids-or-positions...> [--context C] [--source S]
```

**Utility:**

```bash
brain log-location --lat L --lon L [--label L] [--source S]
brain add-event --title T --starts-at D [--ends-at D] [--location L] [--attendees A] [--notes N]
brain embed <entry-id>
brain embed --all [--missing]
```

### Search details

`brain search` does hybrid ranking:
- **70% semantic similarity** via Gemini embeddings (pgvector cosine distance)
- **30% keyword match** via Postgres full-text search (tsvector)
- **+5% boost score** from access_log (time-decayed over 30 days)

Each search records a retrieval ID. Use `brain boost --retrieval <rid> <positions>`
to boost useful results and improve future ranking.

## Connection details

| Setting    | Default                | Env var            |
|------------|------------------------|--------------------|
| DB host    | 127.0.0.1              | `BRAIN_DB_HOST`    |
| DB port    | 5433                   | `BRAIN_DB_PORT`    |
| DB user    | brain                  | `BRAIN_DB_USER`    |
| DB password| brain_local_only       | `BRAIN_DB_PASSWORD`|
| DB name    | zeresh_brain           | `--db` flag        |
| Gemini key | (none)                 | `GEMINI_API_KEY`   |

The DB is hosted on bakkies (albanialink.com) and accessed via SSH tunnel.
On zhizi, the tunnel is provided by zeresh's `openclaw-tunnel` systemd
service, which forwards ports 5433 (brain DB), 18790 (OpenClaw), and
reverse-forwards 2222 (SSH back to zhizi).

## Files

| File            | Purpose                                    |
|-----------------|--------------------------------------------|
| `brain`         | Shell wrapper — activates venv, runs CLI   |
| `brain-cli.py`  | The CLI implementation (click-based)       |
| `brain-mcp`     | Shell wrapper for MCP server mode          |
| `brain-mcp.py`  | MCP server (stdio/SSE) — optional legacy   |
| `requirements.txt` | Python dependencies                     |
| `docker/`       | Docker compose for the Postgres instance   |
| `docs/`         | Specs, plans, setup guides                 |

## Design decisions

- **CLI over MCP**: The brain was originally exposed via an MCP server, but
  MCP stdio connections drop during long Claude Code sessions (context
  compression, memory pressure). The CLI is stateless and trivially
  resilient — every call is independent.
- **Gemini embeddings**: Cheap (~$0.01/month), good quality, 768 dimensions.
  Embeddings are generated on write and on-demand via `brain embed`.
- **Soft deletes**: `brain forget` sets `expires_at` rather than deleting
  rows. All queries filter on expiry automatically.
- **Boost system**: Search results can be boosted to improve future ranking.
  Boosts decay over 30 days to prevent stale entries from dominating.
