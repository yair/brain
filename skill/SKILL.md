---
name: brain
description: >
  Brain — shared persistent memory across all AI sessions. Search,
  store, and recall decisions, facts, TODOs, insights, observations,
  preferences, and debriefs. Look up entities (people, projects, tools),
  check calendar events, or find the latest location. Use this skill whenever
  the conversation involves cross-session knowledge, recalling past decisions,
  storing something for future sessions, project context, persistent TODOs,
  entity lookups, or anything that should survive beyond this conversation.
  Also use when the user says "remember this", "what did we decide",
  "check the brain", "search for", "any TODOs", or asks about people,
  projects, or past work — even if they don't mention "brain" explicitly.
user-invocable: true
allowed-tools: Bash Read
argument-hint: "<command> [args] — e.g. 'search topic', 'recent --project myproj', 'context myproj'"
---

# Brain CLI — Shared Memory for All Sessions

The `brain` CLI is the single source of truth for persistent knowledge
across all AI sessions. Every Claude Code session, agent, briefing, and
bot reads and writes to the same database. Anything worth knowing beyond
this conversation belongs in the brain.

## How it works

The brain is a Postgres database (with pgvector for semantic search).
The `brain` CLI connects directly — no server process, no MCP, no
connection to drop. Each invocation is stateless and independent.

## Core workflow

**On session start**, load project context so you're not starting blind:
```bash
brain --json context <project-slug>
```
This returns recent decisions, open TODOs, related entities, and recent
activity for the project. It replaces reading MEMORY.md for project context.

**During work**, store anything a future session would benefit from knowing:
```bash
brain --json remember --kind decision --title "Use Rust for parser" \
  --body "Python was too slow for the 50k-line files. Rust parser runs in <1s." \
  --source claude-code --project my-project --tags "parser,performance"
```
Decisions, facts, insights, preferences — if it's non-obvious and matters
beyond this conversation, remember it. Future sessions will find it via
search or context dumps.

**When you need past knowledge**, search before guessing:
```bash
brain --json search "parser approach for large files" \
  --source claude-code \
  --session-key code:your-repo-name \
  --context "investigating parser choice for current task"
```
Search uses hybrid semantic + keyword ranking. If a result helps, boost it
so it ranks higher next time:
```bash
brain --json boost --retrieval <rid> 1 3 --source claude-code
```

**About the search flags** — every search is logged to `recall_log` so the
dreaming pipeline can analyze recall patterns. The more descriptive these
flags are, the better dreaming can score and cluster:

- `--source` — who's calling. Always `claude-code` from Claude Code sessions.
- `--session-key` — an OC-style scene key: `agent:main:telegram`,
  `code:brain-repo`, `hook:mail-triage`. Stable human-meaningful name, **not
  a raw UUID**. Omit if you genuinely don't have one.
- `--context` — freeform description of *why* you're searching
  ("debugging the token refresh bug", "looking for prior X decision").

Missing any of these just means `NULL` in the log — never breaks search.
But filling them in is one of the cheapest forms of help you can give
future sessions.

## Always use --json

The brain has human-readable terminal output, but from Claude Code sessions
always pass `--json` so output is structured and parseable.

## Quick reference

| Task | Command |
|------|---------|
| Project context | `brain --json context <project>` |
| Search | `brain --json search "query" --source claude-code [--session-key K] [--context C] [--kind K] [--project P] [--by AUTHOR] [--limit N]` |
| Recent entries | `brain --json recent [--kind K] [--status S] [--project P] [--by AUTHOR] [--since S]` |
| Get by ID | `brain --json get <uuid>` |
| Open TODOs | `brain --json todos [--project P]` |
| All entities | `brain --json entities [--include-deleted]` |
| One entity | `brain --json entity <slug>` |
| Add entity | `brain --json add-entity --id <slug> --kind K --name N [--metadata JSON]` |
| Update entity | `brain --json update-entity <slug> [--name N] [--merge-metadata JSON]` |
| Forget entity | `brain --json forget-entity <slug>` |
| Events | `brain --json events [--from D] [--to D] [--include-deleted]` |
| Add event | `brain --json add-event --title T --starts-at "YYYY-MM-DD HH:MM" [--location L]` |
| Update event | `brain --json update-event <id> [--title T] [--starts-at D] [--location L]` |
| Cancel event | `brain --json cancel-event <id>` |
| Latest location | `brain --json where` |
| Stats overview | `brain --json stats` |
| Remember | `brain --json remember --kind K --title T --body B --source claude-code [--project P] [--tags T]` |
| Update | `brain --json update <id> [--status S] [--body B] [--title T]` |
| Supersede | `brain --json supersede <old-id> --title T --body B` |
| Forget | `brain --json forget <id>` |
| Boost | `brain --json boost --retrieval <rid> <positions> --source claude-code` |

The `--full` global flag disables the 200-char body truncation on search/get/recent/context (handy for humans reading in the terminal; `--json` already returns full bodies).

For the full CLI reference with all flags and examples, read
`references/cli-reference.md` in this skill directory.

## Entry kinds

- **decision** — a choice made during work, with reasoning
- **fact** — something true and useful (a person's birthday, a config value)
- **todo** — a persistent task (use status: active/done/blocked)
- **insight** — a realization or pattern noticed
- **observation** — something seen or reported (email arrived, build failed)
- **preference** — how someone likes things done
- **debrief** — a post-session or post-incident summary

## Writing good entries

Titles should be specific and stand alone — "Rust parser for 50k-line files"
not "parser decision". Future sessions will scan titles in search results,
and vague titles waste everyone's time.

Always set `--source claude-code` so the provenance is clear. Always set
`--project` when the entry belongs to a project. Tag generously — tags
and entity-refs power filtering.

Search before creating — if a relevant entry already exists, `update` or
`supersede` it rather than creating a duplicate.

## Troubleshooting

If brain commands fail with "cannot connect to database", the database
is unreachable. If it runs on a remote server via SSH tunnel, check the
tunnel is up:
```bash
ss -tlnp | grep $BRAIN_DB_PORT
```

## Handling /brain invocations

When invoked as `/brain <args>`, run the brain command with those arguments:
```bash
brain --json <args>
```
If no arguments are given, run `brain --json stats` to show an overview.
