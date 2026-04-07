# Brain — Shared Memory System

*A structured, queryable memory layer accessible to every AI session.*

## The Problem

Memory is scattered:
- **MEMORY.md** — manually curated, gets stale within days
- **Session transcripts** — raw conversation dumps, terrible signal-to-noise
- **Daily memory files** — append-only logs, rarely re-read
- **TODO files** — drift out of sync because no session reliably checks them
- **Briefing context** — doesn't know where the user is, what happened yesterday, what meetings are coming

Every new session starts nearly blind. Sessions working on project A can't
see what project B sessions decided. The information exists somewhere — in
a transcript, a memory file, a TODO — but nothing connects it.

## The Solution

A single Postgres database with vector embeddings that any AI session can
read from and write to. Think of it as a shared brain with different
access levels.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                        brain                                  │
│                   (Postgres + pgvector)                        │
│                                                               │
│  ┌─────────┐ ┌──────────┐ ┌────────┐ ┌──────────┐           │
│  │ entries │ │ entities │ │ events │ │ location │           │
│  │         │ │          │ │        │ │          │           │
│  │decisions│ │ people   │ │calendar│ │ GPS/wifi │           │
│  │insights │ │ projects │ │meetings│ │ presence │           │
│  │ facts   │ │ tools    │ │ todos  │ │          │           │
│  │debriefs │ │ clients  │ │ crons  │ │          │           │
│  └─────────┘ └──────────┘ └────────┘ └──────────┘           │
│                                                               │
│  Vector index (pgvector) for semantic search                  │
│  Full-text index for keyword search                           │
│  Temporal index for time-range queries                        │
└──────────────────┬───────────────────────────────────────────┘
                   │ CLI / MCP / Skill
                   │
    ┌──────────────┼──────────────────────────────┐
    │              │              │                │
┌───▼───┐  ┌──────▼─────┐  ┌────▼────┐  ┌───────▼────────┐
│ Main  │  │Claude Code │  │ Other   │  │  Worker agents │
│ agent │  │ sessions   │  │ agents  │  │  (briefings,   │
│       │  │            │  │         │  │   triage, crons│
│ R/W   │  │  R/W       │  │ R only  │  │  R + limited W │
└───────┘  └────────────┘  └─────────┘  └────────────────┘
```

## Data Model

### `entries` — The core table
```sql
CREATE TABLE entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT NOT NULL,  -- decision, insight, fact, debrief, todo, observation, preference
    source      TEXT NOT NULL,  -- who wrote it: claude-code, cli, agent-name, etc.
    session_id  TEXT,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    tags        TEXT[],
    project     TEXT,
    entity_refs TEXT[],
    embedding   vector(768),
    tsv         tsvector,  -- auto-generated full-text search vector
    confidence  FLOAT DEFAULT 1.0,
    superseded_by UUID,
    expires_at  TIMESTAMPTZ,
    status      TEXT DEFAULT 'active'
);
```

### `entities` — People, projects, tools
```sql
CREATE TABLE entities (
    id       TEXT PRIMARY KEY,    -- slug: alice, my-project, postgres
    kind     TEXT NOT NULL,       -- person, project, client, tool, place
    name     TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'
);
```

### `events` — Calendar, meetings, deadlines
```sql
CREATE TABLE events (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title      TEXT NOT NULL,
    starts_at  TIMESTAMPTZ NOT NULL,
    ends_at    TIMESTAMPTZ,
    location   TEXT,
    attendees  TEXT[],
    notes      TEXT,
    source     TEXT
);
```

### `location` — Presence tracking
```sql
CREATE TABLE location (
    timestamp  TIMESTAMPTZ NOT NULL,
    source     TEXT NOT NULL,
    lat        DOUBLE PRECISION,
    lon        DOUBLE PRECISION,
    accuracy_m FLOAT,
    label      TEXT,
    raw        JSONB
);
```

## Access Patterns

### On session start
```
brain context <project>  → decisions + todos + entities + recent
brain events             → schedule awareness
brain todos              → open tasks
brain recent --limit 5   → what recently happened
```

### During work
```
Decision made → brain remember --kind decision ...
New TODO      → brain remember --kind todo ...
TODO done     → brain update <id> --status done
Context learned → brain remember --kind fact ...
```

### Cross-session search
```
brain search "what approach for X"  → hybrid semantic + keyword
brain boost --retrieval <rid> 1 3   → improve future ranking
```

## Migration Plan

### Phase 1: Infrastructure
- [ ] Deploy Postgres + pgvector (Docker Compose provided)
- [ ] Create schema (init scripts provided)
- [ ] Install CLI and verify

### Phase 2: Seed the Brain
- [ ] Import key entries from existing memory/TODO files
- [ ] Add entities (people, projects, tools)
- [ ] Add upcoming events

### Phase 3: Wire Up Sessions
- [ ] Install CLI skill in Claude Code
- [ ] Configure other agents with CLI or MCP access
- [ ] Update agent instructions to query brain before responding

### Phase 4: Deprecate Old Memory
- [ ] MEMORY.md → generated from brain, no longer source of truth
- [ ] TODO files → brain todos
- [ ] Daily memory files → brain entries

## Cost

- Postgres + pgvector: **$0** (self-hosted)
- Gemini embeddings: **~$0.01-0.05/month**
- Total: **~$0.05/month**

## Why This Is Different

| Aspect | File-based memory | Brain |
|--------|-------------------|-------|
| Source of truth | Markdown files | Database |
| Structure | Freeform | Typed entries + metadata |
| Cross-session | Manual copy | Instant shared access |
| Query | Grep / read whole file | Semantic + structured filters |
| Freshness | Depends who updated | Write-on-change, always current |
| Location-aware | No | Yes (location table) |
| Calendar-aware | No | Yes (events table) |
