# Zeresh Brain — Shared Memory System

*A structured, queryable memory layer accessible to every AI session in Jay's ecosystem.*

## The Problem

Right now, memory is scattered:
- **MEMORY.md** — manually curated, gets stale within days
- **Session transcripts** (1063+ JSONL files) — raw conversation dumps, terrible signal-to-noise
- **Daily memory files** — append-only logs, rarely re-read
- **TODO files** — drift out of sync because no session reliably checks them
- **Briefing context** — doesn't know where Jay is, what he did yesterday, what meetings are coming

Every new session starts nearly blind. Claude Code sessions working on HandWave can't see what Abelard sessions decided. The morning briefing doesn't know Jay biked yesterday. Shiri's agent doesn't know Jay is in a full-day workshop. The information exists somewhere — in a transcript, a memory file, a TODO — but nothing connects it.

## The Solution

A single Postgres database with vector embeddings that any AI session can read from and write to, via MCP protocol. Think of it as a shared brain with different access levels.

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                      zeresh-brain                             │
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
                   │ MCP Protocol
                   │
    ┌──────────────┼──────────────────────────────┐
    │              │              │                │
┌───▼───┐  ┌──────▼─────┐  ┌────▼────┐  ┌───────▼────────┐
│Zeresh │  │Claude Code │  │ Shiri's │  │ Sonnet minions │
│(main) │  │ sessions   │  │ agent   │  │ (briefings,    │
│       │  │ (HandWave, │  │         │  │  triage, crons)│
│ R/W   │  │  Abelard)  │  │ R only  │  │  R + limited W │
└───────┘  └────────────┘  └─────────┘  └────────────────┘
```

## Data Model

### `entries` — The core table
```sql
CREATE TABLE entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Classification
    kind        TEXT NOT NULL,  -- decision, insight, fact, debrief, todo, observation, preference
    source      TEXT NOT NULL,  -- who wrote it: zeresh, jay, claude-code, sonnet-worker, triage, shiri-agent
    session_id  TEXT,           -- originating session (for provenance)
    
    -- Content
    title       TEXT NOT NULL,  -- short, searchable
    body        TEXT NOT NULL,  -- full content
    tags        TEXT[],         -- freeform tags: [handwave, abelard, infrastructure, personal]
    
    -- Relations
    project     TEXT,           -- handwave, abelard, albania-guide, telemetry, etc.
    entity_refs TEXT[],         -- referenced entities: [jay, shiri, art-staliarou, matiss]
    
    -- Search
    embedding   vector(768),   -- Gemini text-embedding-004 (same as current OC setup)
    tsv         tsvector,      -- full-text search vector (auto-generated)
    
    -- Lifecycle
    confidence  FLOAT DEFAULT 1.0,  -- 0-1, decays or gets updated
    superseded_by UUID,              -- points to newer entry that replaces this one
    expires_at  TIMESTAMPTZ          -- optional TTL (e.g. "Jay is in Shijak today")
);
```

### `entities` — People, projects, tools
```sql
CREATE TABLE entities (
    id          TEXT PRIMARY KEY,    -- slug: jay, shiri, art-staliarou, handwave, abelard
    kind        TEXT NOT NULL,       -- person, project, client, tool, place
    name        TEXT NOT NULL,       -- display name
    metadata    JSONB DEFAULT '{}',  -- flexible: email, timezone, role, etc.
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### `events` — Calendar, meetings, deadlines
```sql
CREATE TABLE events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    starts_at   TIMESTAMPTZ NOT NULL,
    ends_at     TIMESTAMPTZ,
    location    TEXT,
    attendees   TEXT[],          -- entity refs
    notes       TEXT,
    source      TEXT,            -- email, manual, calendar-sync
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### `location` — Where is Jay right now?
```sql
CREATE TABLE location (
    id          BIGSERIAL PRIMARY KEY,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    source      TEXT NOT NULL,       -- owntracks, wifi-inference, manual
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION,
    accuracy_m  FLOAT,
    label       TEXT,                -- home, shijak, office, unknown
    raw         JSONB                -- full OwnTracks payload
);
```

## MCP Server

A lightweight MCP server (Node.js or Python) exposes these tools:

### Read tools (everyone gets these)
- `brain.search(query, filters?)` — semantic + keyword hybrid search
  - filters: kind, project, tags, since, until, source
  - returns top-K entries with relevance scores
- `brain.get_entity(id)` — get entity details
- `brain.recent(kind?, project?, limit?)` — latest entries by type
- `brain.events(from?, to?)` — upcoming/recent events
- `brain.where_is_jay()` — latest location + label
- `brain.todos(project?, status?)` — structured TODO query
- `brain.context(project)` — comprehensive project context dump

### Write tools (restricted)
- `brain.remember(kind, title, body, tags?, project?, entity_refs?)` — create entry
- `brain.update(id, patches)` — update existing entry
- `brain.supersede(old_id, new_entry)` — mark old entry replaced, create new one
- `brain.forget(id)` — soft delete (mark expired)
- `brain.log_location(lat, lon, source, label?)` — location update
- `brain.add_event(title, starts_at, ...)` — calendar event

## Access Control

| Agent | Read | Write | Notes |
|-------|------|-------|-------|
| **Zeresh (main)** | Full | Full | Primary brain operator |
| **Claude Code sessions** | Full | Scoped | Write decisions/insights tagged to their project only |
| **Sonnet minions** | Full | Limited | Write observations, can't modify decisions |
| **Junior (triage)** | Read entries + events | Log observations | "New email from Art" → observation |
| **Shiri's agent** | Filtered | Filtered | Weather, events, shared-tagged entries only |
| **Jay (via any session)** | Full | Full | It's his brain |

Access control via MCP server middleware — each client identifies with agent ID, server enforces permissions.

## What Changes Per Agent

### Zeresh (main session)
**Before every response:**
```
brain.where_is_jay()     → location-aware context
brain.events(today, tomorrow) → schedule awareness
brain.recent(kind="todo", status="open", limit=10) → current todos
brain.recent(limit=5)    → what recently happened
```

**After significant interactions:**
```
Decision made → brain.remember(kind="decision", ...)
New TODO      → brain.remember(kind="todo", ...)
TODO done     → brain.update(id, {status: "done"})
Context learned → brain.remember(kind="fact", ...)
```

Replaces: reading MEMORY.md, TODO-ZERESH.md, TODO-JAY.md, memory/*.md.
Those files become *generated views*, not the source of truth.

### Morning Briefing (Sonnet → Opus)
**Query phase (Sonnet):**
```
brain.where_is_jay()      → weather for ACTUAL location, not hardcoded Tirana
brain.events(today)       → meetings to mention
brain.todos(status="open") → always current, never stale
brain.recent(kind="decision", since="3 days ago") → recent decisions
```

Fixes: stale TODOs, wrong city weather, missing meetings, repeated news.

### Claude Code Sessions (HandWave, Abelard, etc.)
**On start:**
```
brain.context("handwave") → all recent decisions, open questions, entities
```

**During work:**
```
brain.remember(kind="decision", project="handwave", title="AGC: zone-weighted ROI", ...)
```

A new CC session instantly knows what the last session decided.

### Junior (triage)
```
brain.remember(kind="observation", title="Email from Art: answers", tags=["handwave"])
brain.search("recent notifications about handwave") → avoid double-alerting
```

### Shiri's Agent
**Reads:** weather, shared events, Jay's location (is he home?)
**Doesn't read:** financial data, work projects, private entries
**Writes:** her preferences, reminders, requests tagged "shiri"

## Migration Plan

### Phase 1: Infrastructure (1-2 hours)
- [ ] Add zeresh-brain service to docker-compose (Postgres 16 + pgvector)
- [ ] Create schema
- [ ] Build MCP server (minimal: search, remember, recent, where_is_jay)
- [ ] Test with openclaw CLI

### Phase 2: Seed the Brain (1 hour)
- [ ] Import key entries from MEMORY.md
- [ ] Import TODOs from TODO-ZERESH.md and TODO-JAY.md
- [ ] Import entities (Jay, Shiri, Art, Matiss, etc.)
- [ ] Import upcoming events
- [ ] Set up OwnTracks → location table (from telemetry plan)

### Phase 3: Wire Up Main Agent (Zeresh)
- [ ] Add MCP server to OC config
- [ ] Update AGENTS.md: "query brain before responding"
- [ ] Update heartbeat: query brain instead of reading TODO files
- [ ] Briefing CONTEXT.md: query brain for TODOs, events, location

### Phase 4: Wire Up Satellites
- [ ] Claude Code: .mcp.json in project roots
- [ ] Junior: brain observation writes
- [ ] Shiri: filtered MCP access
- [ ] Sonnet minions: read access for briefings/crons

### Phase 5: Deprecate Old Memory
- [ ] MEMORY.md → generated weekly from brain
- [ ] TODO files → generated views (or human-readable fallback)
- [ ] Daily memory files → brain entries instead
- [ ] Session transcripts → kept for provenance, brain is the index

## Cost

- Postgres + pgvector: **$0** (runs on bakkies)
- Gemini embeddings: **~$0.01-0.05/month** (already used)
- MCP server: **$0** (lightweight process)
- Total: **~$0.05/month**

## Open Questions

1. **MCP in OC** — does OC have native MCP client support? Need to check docs.
2. **Embedding model** — stick with Gemini, or local model on zhizi?
3. **Conflict resolution** — two sessions write contradictory info, who wins?
4. **Backup** — brain DB in existing backup script?
5. **Shiri privacy** — whitelist (shared-tagged only) or blacklist (everything except work)?
6. **Claude Code MCP** — per-project .mcp.json or global config?
7. **Query budget** — track brain queries per session?
8. **Telemetry convergence** — merge with telemetry stack plan (same Postgres)?

## Why This Is Different

| Aspect | Current (OC memory) | Brain |
|--------|---------------------|-------|
| Source of truth | Files (MEMORY.md, TODOs) | Database |
| Structure | Freeform markdown | Typed entries + metadata |
| Cross-tool | OC only | Any MCP client |
| Query | Semantic over raw text | Semantic + structured filters |
| Freshness | Depends who updated | Write-on-change, always current |
| Location-aware | No | Yes (OwnTracks) |
| Calendar-aware | No | Yes (events table) |
| Access control | None | Per-agent permissions |

The brain doesn't replace OC's transcript search — it's a curated, structured, always-current layer that every session checks first.

---

*"The people who are 10x more effective with AI aren't writing 10x better prompts. They've built 10x better context infrastructure."* — Nate B. Jones
