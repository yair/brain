# brain-cli — Specification for Claude Code

## What to Build

A Python CLI tool (`brain-cli.py`) that provides read/write access to the zeresh-brain PostgreSQL database. This is the shared backend for all AI agents in the system.

## Installation Target

- Single file: `/home/oc/projects/zeresh-brain/brain-cli.py`
- Symlink to: `/home/oc/.local/bin/brain` (create dir if needed, it's in PATH)
- Dependencies (already installed): `psycopg2-binary`, `click`, `requests`

## Database Connection

- Host: `127.0.0.1`
- Port: `5433`
- User: `brain`
- Password: `brain_local_only`
- Database: `zeresh_brain` (default, `--db` flag to switch to e.g. `fay_brain`)

Use `psycopg2` (sync). No need for async.

## Existing Schema

The database already exists with data. Do NOT create or modify tables. See PLAN.md for full schema, but the key tables:

- `entries` — decisions, facts, insights, todos, observations (14 rows exist)
- `entities` — people, projects, tools (12 rows exist)
- `events` — calendar items (2 rows exist)
- `location` — GPS/presence data (empty)

Important columns on `entries`:
- `id` (UUID), `kind` (text), `source` (text), `title` (text), `body` (text)
- `tags` (text[]), `project` (text), `entity_refs` (text[])
- `embedding` (vector(768)), `tsv` (tsvector)
- `status` (text), `confidence` (float), `superseded_by` (UUID), `expires_at` (timestamptz)

## Commands to Implement

### Read Commands

```bash
brain search "what is jay working on"        # Hybrid semantic + keyword search
brain search "handwave" --kind decision       # With filters
brain search "todo" --project abelard --since "3 days ago"

brain recent                                  # Last 10 entries
brain recent --kind todo --status open        # Open TODOs
brain recent --project handwave --limit 5     # Project-scoped
brain recent --kind decision --since "1 week ago"

brain get <id>                                # Get single entry by UUID
brain entity <slug>                           # Get entity (e.g., "brain entity jay")
brain entities                                # List all entities

brain events                                  # Upcoming events (next 7 days default)
brain events --from today --to "next week"

brain todos                                   # Alias: brain recent --kind todo --status open
brain todos --project handwave

brain where-is-jay                            # Latest location entry

brain context <project>                       # Dump: recent decisions + todos + entities for a project
```

### Write Commands

```bash
brain remember --kind decision --title "AGC approach" --body "Zone-weighted ROI..." --project handwave --tags handwave,camera
brain remember --kind todo --title "Fix backup script" --body "Add fay_brain" --status open
brain remember --kind fact --title "Jay's birthday" --body "February 21, 1975"
brain remember --kind observation --title "Email from Art" --body "Replied with answers" --source junior

brain update <id> --status done               # Mark TODO done
brain update <id> --body "Updated content"    # Update body
brain update <id> --confidence 0.5            # Lower confidence

brain supersede <old-id> --title "New approach" --body "..."  # Replace entry

brain forget <id>                             # Soft delete (set expires_at to now)

brain log-location --lat 41.3275 --lon 19.8187 --label "home" --source manual
brain add-event --title "HandWave standup" --starts-at "2026-03-10 09:00" --attendees jay,matiss
```

### Utility Commands

```bash
brain stats                                   # Count of entries by kind, entities, events
brain embed <id>                              # Generate/update embedding for a single entry
brain embed --all --missing                   # Generate embeddings for all entries missing them
```

## Semantic Search (Embeddings)

Use the **Gemini text-embedding-004** API for embeddings:
- Endpoint: `https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent`
- API key: `AIzaSyAYkIwuj_GbD1Vx63hkWT0jleiXAXJTuSQ`
- Dimension: 768

For `brain search`:
1. Generate embedding for the query text
2. Run hybrid search: combine vector similarity (`embedding <=> query_embedding`) with full-text (`tsv @@ plainto_tsquery(query)`)
3. Rank by weighted combination (vector 0.7, FTS 0.3)
4. Apply any filters (kind, project, tags, since/until)
5. Return top-K results (default 10)

For `brain remember`:
- Auto-generate embedding from `title + " " + body` on write
- Also auto-generate `tsv` (the DB may have a trigger for this — check, if not, set it manually)

## Output Format

- Default: human-readable (good terminal output with colors if tty)
- `--json` flag: JSON output (for programmatic use by agents)
- `--quiet` flag: minimal output (just IDs for writes, just titles for reads)

## Source Field

When `--source` is not specified, default to `"cli"`. Agents will pass their identity:
- `brain remember --source zeresh ...`
- `brain remember --source claude-code ...`
- `brain remember --source junior ...`

## Error Handling

- DB connection errors: clear message, exit 1
- Embedding API errors: warn but still write the entry (embedding can be added later with `brain embed`)
- Invalid filters: show usage help, exit 2

## Testing

After building, test with:
1. `brain stats` — should show existing data counts
2. `brain recent` — should list existing entries
3. `brain search "handwave"` — should find relevant entries
4. `brain remember --kind test --title "Test entry" --body "Testing brain-cli" --tags test`
5. `brain search "test entry"` — should find it
6. `brain forget <id-from-step-4>` — clean up

## Code Style

- Single file, well-organized with click command groups
- Type hints
- Docstrings on commands
- No over-engineering — this is a CLI tool, not a framework
