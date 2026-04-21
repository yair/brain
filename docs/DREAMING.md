# Brain Dreaming — Memory Consolidation Sub-Project

*Design document for importing OC's Dreaming architecture into the Brain system*
*Jay & Code, April 2026*

## Problem Statement

Brain accumulates entries without systematic review. TODOs go stale, facts
become outdated, insights are never acted on, and the signal-to-noise ratio
degrades over time. Nobody — human or agent — is good at maintenance.

Meanwhile, OC has built a sophisticated 3-phase memory consolidation pipeline
("Dreaming") that solves exactly this problem — but for Markdown files, not
databases. We want the architecture without the implementation.

## What OC Dreaming Does (Reference Implementation)

### Three Phases

**Light Sleep** (Ingestion & Staging)
- Reads recent daily memory files and session transcripts
- Parses into snippet chunks, deduplicates via Jaccard similarity (0.9 threshold)
- Stages candidates in short-term recall store (SQLite + sqlite-vec)
- Records "light phase signal" hits for later ranking boosts
- Never writes to long-term storage

**REM Sleep** (Pattern Recognition)
- Analyzes staged material from a configurable lookback window (default 7 days)
- Extracts recurring themes by examining concept tag frequency
- Identifies "candidate truths" — high-confidence repetitive patterns
- Records REM signal hits that boost deep-phase scoring
- Optionally generates a narrative "dream diary" entry via background LLM call
- Up to 3 signals that pass stringent thresholds are surfaced to the agent
  ("remembered dreams") — the rest stays subconscious
- Never writes to long-term storage

**Deep Sleep** (Promotion / Consolidation)
- Scores all candidates using 6 weighted signals (see below)
- Applies phase reinforcement boosts from Light/REM hits
- Filters against 3 hard threshold gates
- Rehydrates snippets from live files before writing (removes stale content)
- **Only phase that writes to long-term storage** (MEMORY.md in OC, brain entries for us)

### Promotion Scoring (6 Signals)

| Signal | Weight | What it measures |
|--------|--------|------------------|
| Relevance | 0.30 | Average retrieval quality when this memory was recalled |
| Frequency | 0.24 | How often the signal appeared in recall traces |
| Query diversity | 0.15 | Distinct query/day contexts that triggered recall |
| Recency | 0.15 | Time-decayed freshness (14-day half-life) |
| Consolidation | 0.10 | Multi-day recurrence strength |
| Conceptual richness | 0.06 | Concept-tag density |

Phase boosts: Light +0.05 max, REM +0.08 max (both recency-decayed).

### Three Hard Gates (ALL must pass for promotion)

- `minScore: 0.8` — composite weighted score
- `minRecallCount: 3` — minimum recall signals
- `minUniqueQueries: 3` — distinct query contexts

Candidates failing gates remain in short-term store, accumulating signals
until promotion or expiration at `maxAgeDays` (default 30).

### Subconscious vs Conscious

Most dreaming output stays internal — the agent never sees it. Only up to 3
signals that pass stringent thresholds are surfaced as "remembered dreams."
These can be:
- Journaled (written to a dream diary)
- Posted to a channel
- Sent to the human counterpart
- Used to update the agent's session context

This separation is important: the consolidation machinery should work
continuously without polluting the agent's context or the human's notifications.

## What Brain Already Has

### Database Schema (PostgreSQL + pgvector + TimescaleDB)

**`entries`** — The core memory table
- Typed: kind (fact/decision/todo/insight/observation/preference/debrief)
- Structured: title, body, tags, project, entity_refs
- Searchable: embedding (Gemini 768-dim via pgvector), tsvector (BM25)
- Lifecycle: confidence, superseded_by, expires_at, status (active/superseded/expired/deleted)

**`retrievals`** — Search result tracking (ALREADY EXISTS, UNDERUSED)
- Records every search query and which entry IDs were returned
- Currently only used for positional boosting in search
- Could be the foundation for recall frequency tracking

**`access_log`** — Boost/citation tracking (ALREADY EXISTS, UNDERUSED)
- Records when entries are accessed, cited, or acted on
- Has `kind` field: boost, cited, acted_on
- Could track recall quality and context

**`entities`** — People, projects, tools (structured, queryable)

**`events`** — Calendar items

**`location`** — GPS tracking (via Plexus/OwnTracks, TimescaleDB hypertable)

**`telemetry`** — Sensor data (UPS, phone, etc.)

### What's Missing

1. **Recall tracking at query time** — `retrievals` stores result sets but not
   per-entry recall scores. We need to log: which entry was returned, at what
   rank, for what query, in what session context.

2. **Signal accumulation** — No equivalent of OC's short-term recall store.
   Entries go straight to permanent storage with no staging area.

3. **Automated scoring** — No composite score for entry importance. Entries
   have `confidence` but it's manually set and never updated.

4. **Consolidation pipeline** — No mechanism to merge related entries, mark
   stale ones, or create summaries from patterns.

5. **Session transcript ingestion** — OC sessions generate conversation history
   but it's never processed into brain entries (except manually).

6. **Expiration enforcement** — `expires_at` and `superseded_by` fields exist
   but nothing checks or acts on them.

## Proposed Architecture

### Phase 0: Recall Tracking (Foundation)

Before any dreaming can work, we need data about what gets recalled and how.

**New table: `recall_log`**
```sql
CREATE TABLE recall_log (
    id          BIGSERIAL PRIMARY KEY,
    entry_id    UUID NOT NULL REFERENCES entries(id),
    query       TEXT NOT NULL,           -- the search query that found this entry
    rank        INT,                     -- position in results (1 = top)
    score       FLOAT,                   -- retrieval similarity score
    session_id  TEXT,                    -- which session triggered the search
    source      TEXT,                    -- who searched: zeresh, fay, david, claude-code
    context     TEXT,                    -- optional: what was being discussed
    recalled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_recall_log_entry ON recall_log(entry_id);
CREATE INDEX idx_recall_log_time ON recall_log(recalled_at DESC);
```

**Integration point**: Modify `brain_search` in the MCP server to log every
search result to `recall_log` before returning. Zero-cost for the caller —
the logging is a side effect of searching.

This gives us the raw signal data that all 6 scoring dimensions derive from.

### Phase 1: Light Sleep (Ingestion & Signal Staging)

**Trigger**: Nightly cron (via Vagus or system cron), configurable frequency.

**Input**: 
- `recall_log` entries from the last N days
- Session transcripts (OC JSONL files) from the last N days
- New `entries` created since last run

**Process**:
1. Aggregate recall_log by entry_id → compute per-entry signals:
   - recall_count (frequency)
   - unique_queries (query diversity)
   - avg_score (relevance)
   - unique_days (consolidation)
   - last_recalled (recency)
2. For session transcripts: extract potential new entries (facts, decisions,
   observations mentioned but never explicitly saved). Use one-shot LLM call
   to extract candidates.
3. Stage everything in a `dream_candidates` table:
   ```sql
   CREATE TABLE dream_candidates (
       id              BIGSERIAL PRIMARY KEY,
       entry_id        UUID REFERENCES entries(id),  -- NULL for new candidates
       candidate_text  TEXT,                          -- for new candidates from transcripts
       signals         JSONB NOT NULL,                -- all 6 signal values
       composite_score FLOAT,
       phase_boosts    JSONB DEFAULT '{}',
       status          TEXT DEFAULT 'staged',         -- staged, promoted, expired, rejected
       created_at      TIMESTAMPTZ DEFAULT now(),
       expires_at      TIMESTAMPTZ                    -- maxAgeDays from creation
   );
   ```

**Output**: Populated `dream_candidates` table. No changes to `entries`.

### Phase 2: REM Sleep (Pattern Recognition)

**Input**: `dream_candidates` from Light phase + recent `entries`.

**Process**:
1. Cluster candidates by semantic similarity (pgvector cosine distance)
2. Identify recurring themes across sessions/days
3. Generate "candidate truths" — patterns that appear across multiple contexts
4. Score pattern strength and add REM boost to relevant candidates
5. Optionally: generate 1-3 "dream insights" — natural language observations
   about patterns. These are the "remembered dreams" surfaced to the agent.

**LLM involvement**: One call to analyze the clustered candidates and extract
patterns. Use the default model (currently Mimo for personality, GLM for
analysis — TBD which is better for pattern recognition).

**Output**: Updated `dream_candidates` with REM boosts. Optional dream insights
for surfacing.

### Phase 3: Deep Sleep (Consolidation)

**Input**: `dream_candidates` with full scoring from Light + REM phases.

**Process** (for existing entries):
1. Apply hard gates (minScore, minRecallCount, minUniqueQueries)
2. For entries that pass:
   - If the entry is a TODO and the pattern suggests completion → update status
   - If the entry is a FACT and signals show it's frequently recalled with
     corrections → flag for review or supersede
   - If multiple entries cover the same topic → merge into a consolidated entry
   - Update `confidence` score based on composite score
3. For entries that fail gates after `maxAgeDays`:
   - If never recalled: soft-delete (status → 'expired')
   - If recalled but didn't score high enough: keep, reset timer
4. For new candidates (from transcript extraction):
   - If they pass gates: create new `entries` with appropriate kind/tags
   - Source: 'dreaming' (so agents know this was auto-generated)

**Process** (for the "subconscious" insights):
1. Select top 1-3 dream insights from REM phase
2. If any pass the surfacing threshold:
   - Write to a `dream_diary` table or brain entry (kind: 'debrief', source: 'dreaming')
   - Optionally notify the human via Telegram (Vagus/alert_main)
   - Optionally inject into the agent's next session context

**Output**: Updated `entries` (confidence scores, status changes, merges, new
entries from transcripts). Optional notifications.

## Notification & Surfacing

**To Jay (Telegram)**:
- After each dreaming cycle: brief summary of actions taken
  ("3 entries promoted, 2 TODOs marked stale, 1 new insight discovered")
- Dream insights that pass the surfacing threshold
- Entries flagged for human review (conflicting facts, ambiguous TODOs)

**To Zeresh (session context)**:
- Surfaced dream insights injected at session start
- "You dreamt about X — here's what surfaced" as a natural context item

**Silent (subconscious)**:
- Confidence score updates
- Expiration of stale entries
- Signal accumulation
- Most consolidation work

## Integration Points

### Brain MCP Server + CLI + Skill
- Modify `brain_search` in ALL access points to log to `recall_log` (Phase 0)
- Add `brain dream status` CLI command (check last run, pending candidates)
- Add `brain dream insights` CLI command (surface recent dream insights)
- Add MCP tools for the same

### Session Collector (Pre-Dreaming Task)
- Runs before Light phase, collects sessions from all sources
- OC sessions: `~/.openclaw/agents/*/sessions/*.jsonl`
- Claude Code sessions: `~/.claude/projects/*/sessions/` (bakkies)
- Remote sessions: via SSH from zhizi and any other nodes
- Output: unified transcript staging area for Light phase ingestion
- Must handle: dedup across sources, redaction, format normalization

### Vagus / System Cron
- Dreaming cycle runs daily at 3 AM via system cron
- Session collection runs first (2:30 AM?), then dreaming phases
- Notifications routed via alert_main (Telegram) and notify-session.sh

### OC Sessions
- Session transcript ingestion as input to Light phase
- Dream insights as context injection at session start

### Existing Brain Tools
- `brain_update` — used by Deep phase to modify entries
- `brain_forget` — used by Deep phase to expire stale entries
- `brain_boost` — already tracks access patterns (extend for recall tracking)

## Design Decisions (Resolved)

1. **Model**: Opus via `claude -p` (non-interactive print mode). Configurable
   but Opus is the primary — Mimo/GLM/OpenRouter models only as fallback.
   They're not up to the analytical task (GLM acknowledged todo updates but
   did nothing; Mimo couldn't find referenced emails).

2. **Frequency**: Daily at 3 AM. Even on quiet days — REM/Deep cycles can
   still find value in existing accumulated signals without new input.

3. **Cross-agent dreaming**: No. Each agent's brain is independent.

4. **Retroactive scoring**: Yes, backfill from the `retrievals` table.
   Also: do a one-time consolidation pass over ALL brain entries before
   letting dreaming maintain ongoing. There are many contradicting and
   stale entries that need cleaning first.

5. **Thresholds**: Start with minRecallCount=5 and maxAgeDays=90.
   The higher count and longer window should help process old entries
   gradually rather than mass-expiring everything.

6. **Dream diary format**: Brain entries (kind: 'debrief', source: 'dreaming').
   Not pollution — sometimes dream insights are the best search result.

7. **Conflict with manual edits**: If an entry was manually edited (by
   Zeresh, Jay, Code, or anyone), its dreaming scores should be ZEROED.
   It's effectively a new entry and needs to be reassessed from scratch.
   Check `updated_at > created_at` AND `updated_at > last_dream_score_at`.

## Additional Design Notes

### LLM Access

One-shot LLM calls via `claude -p` (Claude Code print mode). This gives us
Opus without managing API keys or sessions. The command should be configurable
— fallback to OpenRouter models if Opus isn't available. Do NOT use OC agents
for dreaming LLM calls — they carry too much context overhead.

### Session Collection

Sessions need to be collected from MULTIPLE sources before dreaming:
- OC sessions: `~/.openclaw/agents/*/sessions/*.jsonl`
- Claude Code sessions on bakkies: `~/.claude/projects/*/sessions/`
- Claude Code sessions on zhizi: collected via SSH/rsync
- Other machines/users: configurable collection endpoints

Session collection is a separate pre-dreaming task. Collected transcripts
go to a staging area before Light phase ingestion.

### Access Points

Brain is accessed via:
- **MCP server** (brain-mcp.py) — used by OC agents and Claude Code
- **CLI** (brain-cli.py) — used by scripts, cron jobs, humans
- **Skill** (brain skill in OC/Code) — wraps CLI for agent use

All three need recall tracking (Phase 0). The MCP server is the most
important since it handles the most searches, but CLI searches should
also be logged.

## Implementation Order

0. **Initial consolidation** — before any dreaming, do a one-time Opus-powered
   review of all brain entries. Find contradictions, stale TODOs, duplicate
   facts, outdated decisions. Clean the house before automating maintenance.
   Backfill recall signals from the `retrievals` table.

1. **Phase 0: Recall tracking** — modify brain MCP server, CLI, AND skill
   to log to `recall_log`. All three access points must track. Zero
   user-facing changes. Start accumulating signal data.

2. **Session collector** — build the pre-dreaming task that gathers
   transcripts from OC, Claude Code, and remote nodes.

3. **Phase 1: Light Sleep** — build the signal aggregation pipeline.
   Read-only analysis of recall_log. Populates dream_candidates.

4. **Phase 3: Deep Sleep (subset)** — start with just expiration:
   entries never recalled in 30+ days get flagged. Low risk.

4. **Phase 2: REM Sleep** — add pattern recognition. Requires LLM calls.
   Most complex, highest value.

5. **Phase 3: Deep Sleep (full)** — merge, consolidate, create new entries
   from transcripts. Highest risk, needs careful testing.

6. **Notification & surfacing** — dream insights to Telegram/session context.

## References

- OC CHANGELOG: `~/.npm-global/lib/node_modules/openclaw/CHANGELOG.md` (grep for "dreaming")
- OC dreaming guide: https://dev.to/czmilo/openclaw-dreaming-guide-2026
- OC dreaming docs: https://docs.openclaw.ai/concepts/dreaming
- OC memory docs: https://docs.openclaw.ai/concepts/memory
- OC dreaming config ref: https://docs.openclaw.ai/reference/memory-config
- `openclaw memory rem-harness --json` — preview REM output (run locally)
- `openclaw memory status` — shows dreaming state per agent (currently all off)
- Brain schema: `~/projects/zeresh-brain/docker/init/002-schema.sql`
- Brain MCP server: `~/projects/zeresh-brain/brain-mcp.py`
- Brain CLI: `~/projects/zeresh-brain/brain-cli.py`
- Brain spec: `~/projects/zeresh-brain/docs/SPEC.md`
- Abelard RL paper: `~/projects/abelard/docs/rl-as-software-development.md`
- RL↔dreaming insight: brain entry f50ded17 ("The dreaming pipeline IS an RL loop")
- OC dreaming architecture analysis: brain entry 43c2bfc1
- CAGC autoresearch (related RL application): brain entry 33c3f356
- Dreaming plan finalized: brain entry cc32c5a7
- Design memory consolidation (original TODO): brain entry 00a509f5
- notify-session.sh (for dream notifications): `~/.openclaw/workspace/scripts/notify-session.sh`
- alert-lib.sh (for Telegram alerts): `~/.openclaw/workspace/scripts/alert-lib.sh`
- Morning briefing CONTEXT (dreams will be added here): `~/.openclaw/workspace/projects/morning-briefing/CONTEXT.md`

---

*"The stated goal was to keep memory clean, and we haven't. But maybe the
scoring system we discovered along the way is the real edge."*

*— Code, April 18, 2026*
