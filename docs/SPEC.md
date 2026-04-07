# brain-cli — Specification

## What to Build

A Python CLI tool (`brain-cli.py`) that provides read/write access to the
brain PostgreSQL database. This is the shared memory backend for AI agents.

## Database Connection

Connection via environment variables (see `.env.example`):
- `BRAIN_DB_HOST` (default: 127.0.0.1)
- `BRAIN_DB_PORT` (default: 5432)
- `BRAIN_DB_USER` (default: brain)
- `BRAIN_DB_PASSWORD`
- `BRAIN_DB_NAME` (default: brain)

Use `psycopg2` (sync). No need for async.

## Existing Schema

The database already exists with data. Do NOT create or modify tables.
See `docker/init/002-schema.sql` for the full schema. Key tables:

- `entries` — decisions, facts, insights, todos, observations
- `entities` — people, projects, tools
- `events` — calendar items
- `location` — GPS/presence data
- `retrievals` — search result sets (for positional boosting)
- `access_log` — boost/citation tracking

## Commands

### Read Commands

```bash
brain search "query"                          # Hybrid semantic + keyword search
brain search "topic" --kind decision          # With filters
brain search "todo" --project myproj --since "3 days ago"

brain recent                                  # Last 10 entries
brain recent --kind todo --status active      # Open TODOs
brain recent --project myproj --limit 5       # Project-scoped

brain get <id>                                # Single entry by UUID
brain entity <slug>                           # Entity by slug
brain entities                                # List all entities

brain events                                  # Upcoming events (7 days)
brain events --from today --to "next week"

brain todos                                   # Open TODOs
brain todos --project myproj

brain where                                   # Latest location

brain context <project>                       # Decisions + todos + entities
```

### Write Commands

```bash
brain remember --kind decision --title "Approach X" --body "Details..." --project myproj --tags tag1,tag2
brain remember --kind todo --title "Fix backup" --body "Add new DB" --status active

brain update <id> --status done
brain update <id> --body "Updated content"

brain supersede <old-id> --title "New approach" --body "..."

brain forget <id>                             # Soft delete

brain log-location --lat 41.33 --lon 19.82 --label "office"
brain add-event --title "Standup" --starts-at "2026-03-10 09:00"
```

### Utility Commands

```bash
brain stats                                   # Entry counts
brain embed <id>                              # Generate/update embedding
brain embed --all --missing                   # Backfill embeddings
brain boost --retrieval <rid> 1 3 5           # Boost useful search results
brain boost-history                           # Most-boosted entries
```

## Semantic Search (Embeddings)

Uses **Gemini gemini-embedding-001** API for 768-dim embeddings:

For `brain search`:
1. Generate embedding for the query text
2. Hybrid ranking: 70% vector similarity + 30% full-text match + 5% boost score
3. Apply filters (kind, project, since)
4. Return top-K results (default 10) with retrieval ID

For `brain remember`:
- Auto-generate embedding from `title + " " + body` on write
- `tsv` is a generated column (auto-updated by Postgres)

## Output Format

- Default: human-readable (colored terminal output)
- `--json`: structured JSON (for programmatic use by agents)
- `--quiet`: minimal output (IDs for writes, titles for reads)

## Source Field

Default source: `"cli"`. Agents should identify themselves:
- `brain remember --source claude-code ...`
- `brain remember --source my-agent ...`

## Error Handling

- DB connection errors: clear message, exit 1
- Embedding API errors: warn but still write (embedding can be added later)
- Invalid filters: usage help, exit 2
