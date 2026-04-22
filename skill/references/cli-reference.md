# Brain CLI — Full Reference

## Global flags

| Flag      | Effect                                    |
|-----------|-------------------------------------------|
| `--json`  | JSON output (always use from AI sessions) |
| `--quiet` | Minimal output (IDs/titles only)          |
| `--full`  | Show full entry/event bodies (no 200-char truncation). `--json` already returns full bodies. |
| `--db`    | Database name (default from `BRAIN_DB_NAME` or `brain`) |

## Search

```bash
brain --json search "query" [--kind KIND] [--project PROJECT] [--since SINCE] [--limit N]
```

Hybrid semantic + keyword search. Ranking: 70% vector similarity (Gemini
embeddings), 30% full-text match, +5% time-decayed boost score from
access_log.

Each search returns a `retrieval_id` in JSON output. Use this with
`brain boost --retrieval <rid> <positions>` to boost useful results.

**--since** accepts relative ("3 days ago", "1 week ago", "yesterday",
"today") or absolute ("2026-04-01", "2026-04-01 14:00") dates.

**--kind** values: decision, fact, todo, insight, observation, preference,
debrief.

## Recent

```bash
brain --json recent [--kind KIND] [--status STATUS] [--project PROJECT] [--source SOURCE] [--since SINCE] [--limit N]
```

Returns entries ordered by creation time, newest first. Default limit: 10.

**--source** filters by who wrote the entry — useful for "what has claude-code
been up to?" or "what did Jay write recently?"

## Get

```bash
brain --json get <entry-id>
```

Fetch a single entry by UUID. Partial UUID prefixes work.

## Context

```bash
brain --json context <project-slug>
```

Returns a structured dump with four sections:
- `decisions` — recent decisions for the project (up to 10)
- `todos` — open TODOs (status=active) for the project
- `entities` — related entities
- `recent` — recent entries of all kinds (up to 10)

This is the recommended first call at session start.

## TODOs

```bash
brain --json todos [--project PROJECT]
```

Shortcut for `recent --kind todo --status active --limit 50`.

## Entities

```bash
brain --json entities [--include-deleted]
brain --json entity <slug> [--include-deleted]
brain --json add-entity --id <slug> --kind KIND --name "Full Name" [--metadata '{"k":"v"}']
brain --json update-entity <slug> [--name N] [--kind K] [--metadata JSON] [--merge-metadata JSON]
brain --json forget-entity <slug>
```

Entities are people, projects, tools, clients, or places. Keyed by slug
(e.g., `alice`, `my-project`, `postgres`).

Entity metadata is stored as JSONB — can contain arbitrary key-value pairs
like email, timezone, role, etc.

**--metadata** replaces the JSONB entirely. **--merge-metadata** shallow-merges
into the existing object (so you don't clobber unrelated keys). Pass one or
the other.

**forget-entity** is a soft-delete. Entries that reference the entity via
`entity_refs` keep their reference intact — the entity just stops appearing
in default listings. Pass `--include-deleted` to show it.

## Events

```bash
brain --json events [--from DATE] [--to DATE] [--include-deleted]
brain --json add-event --title T --starts-at DATETIME [--ends-at D] [--location L] [--attendees "a,b"] [--notes N]
brain --json update-event <id> [--title T] [--starts-at D] [--ends-at D] [--location L] [--attendees "a,b"] [--notes N]
brain --json cancel-event <id>
```

Calendar events. Defaults to today through 7 days from now. **cancel-event**
is a soft-delete (sets `deleted_at`); cancelled events are hidden unless
`--include-deleted` is passed.

## Where (location)

```bash
brain --json where
```

Returns the latest entry from the location table.

## Stats

```bash
brain --json stats
```

Returns counts of entries by kind, total entries, entries with embeddings,
entity count, and event count.

## Remember

```bash
brain --json remember \
  --kind KIND \
  --title "Short searchable title" \
  --body "Full content with details and reasoning" \
  --source claude-code \
  [--project PROJECT] \
  [--tags "tag1,tag2,tag3"] \
  [--entity-refs "alice,my-project"] \
  [--status active] \
  [--confidence 0.9]
```

Creates a new entry. Automatically generates an embedding from title + body
on write (requires GEMINI_API_KEY).

**--kind** (required): decision, fact, todo, insight, observation,
preference, debrief.

**--source**: Always set to `claude-code` from Claude Code sessions.
Other sources: cli, my-agent, worker, etc.

**--status**: Default `active`. For TODOs, use active/done/blocked.

**--confidence**: Float 0-1, default 1.0. Lower for uncertain information.

## Update

```bash
brain --json update <entry-id> [--status S] [--body B] [--title T] [--confidence C]
```

Partial update — only specified fields change.

## Supersede

```bash
brain --json supersede <old-id> --title "New title" --body "New body" [--source S]
```

Creates a new entry inheriting kind/project/tags from the old one, and
sets `superseded_by` on the old entry. Use when an entry needs substantial
revision rather than a minor update.

## Forget

```bash
brain --json forget <entry-id>
```

Soft-delete: sets `expires_at` to now. Entry stops appearing in queries
but remains in the database.

## Boost

```bash
brain --json boost --retrieval <retrieval-id> <positions...> --source claude-code [--context "why"]
brain --json boost <entry-uuid> [<entry-uuid>...] --source claude-code [--context "why"]
```

Records that entries were useful, improving their ranking in future searches.
Boosts decay over 30 days.

**By position**: After a search, use the retrieval_id from the result and
reference results by their position number (1-indexed).

**By UUID**: Boost entries directly by their UUID.

**--kind**: Access type. Default "boost". Also: "cited", "acted_on".

## Boost History

```bash
brain --json boost-history [entry-id] [--limit N]
```

Without entry-id: shows most-boosted entries. With entry-id: shows boost
history for that specific entry.

## Log Location

```bash
brain --json log-location --lat LAT --lon LON [--label LABEL] [--source SOURCE] [--accuracy M]
```

## Embed

```bash
brain --json embed <entry-id>
brain --json embed --all [--missing]
```

Generate or regenerate Gemini embeddings. Use `--all --missing` to backfill
entries that were created without an API key.

## Environment variables

Brain uses role separation: the CLI connects as `brain_cli` (not the
superuser `brain` role). Every client host needs the CLI credentials;
only the admin host needs the admin + dream credentials.

| Variable                  | Default   | Who needs it                | Purpose |
|---------------------------|-----------|-----------------------------|---------|
| `BRAIN_DB_HOST`           | 127.0.0.1 | all clients                 | Database host |
| `BRAIN_DB_PORT`           | 5432      | all clients                 | Database port |
| `BRAIN_DB_NAME`           | brain     | all clients                 | Database name (e.g. `zeresh_brain`) |
| `BRAIN_CLI_DB_USER`       | —         | all clients                 | CLI role (usually `brain_cli`) |
| `BRAIN_CLI_DB_PASSWORD`   | —         | all clients                 | CLI role password |
| `BRAIN_DREAM_DB_USER`     | —         | admin/dreamer host only     | Dreamer role |
| `BRAIN_DREAM_DB_PASSWORD` | —         | admin/dreamer host only     | Dreamer role password |
| `BRAIN_ADMIN_DB_USER`     | —         | admin host + backup scripts | Admin role (superuser) |
| `BRAIN_ADMIN_DB_PASSWORD` | —         | admin host + backup scripts | Admin role password |
| `GEMINI_API_KEY`          | —         | any writer                  | For embedding generation |

The CLI fails loudly if `BRAIN_CLI_DB_USER` / `BRAIN_CLI_DB_PASSWORD`
aren't set — by design, so the transition from the old superuser-everywhere
setup is explicit.
