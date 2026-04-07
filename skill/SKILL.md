---
name: brain
description: >
  Zeresh Brain — shared persistent memory across all AI sessions. Search,
  store, and recall decisions, facts, TODOs, insights, observations,
  preferences, and debriefs. Look up entities (people, projects, tools),
  check calendar events, or find Jay's location. Use this skill whenever
  the conversation involves cross-session knowledge, recalling past decisions,
  storing something for future sessions, project context, persistent TODOs,
  entity lookups, or anything that should survive beyond this conversation.
  Also use when the user says "remember this", "what did we decide",
  "check the brain", "search for", "any TODOs", or asks about people,
  projects, or past work — even if they don't mention "brain" explicitly.
user-invocable: true
allowed-tools: Bash Read
argument-hint: "<command> [args] — e.g. 'search openclaw', 'recent --project zhizi', 'context zhizi'"
---

# Brain CLI — Shared Memory for All Sessions

The `brain` CLI is the single source of truth for persistent knowledge
across Jay's AI ecosystem. Every Claude Code session, OpenClaw agent,
briefing, and triage bot reads and writes to the same database. Anything
worth knowing beyond this conversation belongs in the brain.

## How it works

The brain is a Postgres database (with pgvector for semantic search)
hosted remotely and accessed locally via SSH tunnel on port 5433. The
`brain` CLI connects directly — no server process, no MCP, no connection
to drop. Each invocation is stateless and independent.

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
  --source claude-code --project handwave --tags "parser,performance"
```
Decisions, facts, insights, preferences — if it's non-obvious and matters
beyond this conversation, remember it. Future sessions will find it via
search or context dumps.

**When you need past knowledge**, search before guessing:
```bash
brain --json search "parser approach for large files"
```
Search uses hybrid semantic + keyword ranking. If a result helps, boost it
so it ranks higher next time:
```bash
brain --json boost --retrieval <rid> 1 3 --source claude-code
```

## Always use --json

The brain has human-readable terminal output, but from Claude Code sessions
always pass `--json` so output is structured and parseable.

## Quick reference

| Task | Command |
|------|---------|
| Project context | `brain --json context <project>` |
| Search | `brain --json search "query" [--kind K] [--project P] [--limit N]` |
| Recent entries | `brain --json recent [--kind K] [--status S] [--project P] [--since S]` |
| Get by ID | `brain --json get <uuid>` |
| Open TODOs | `brain --json todos [--project P]` |
| All entities | `brain --json entities` |
| One entity | `brain --json entity <slug>` |
| Events | `brain --json events [--from D] [--to D]` |
| Jay's location | `brain --json where-is-jay` |
| Stats overview | `brain --json stats` |
| Remember | `brain --json remember --kind K --title T --body B --source claude-code [--project P] [--tags T]` |
| Update | `brain --json update <id> [--status S] [--body B] [--title T]` |
| Supersede | `brain --json supersede <old-id> --title T --body B` |
| Forget | `brain --json forget <id>` |
| Boost | `brain --json boost --retrieval <rid> <positions> --source claude-code` |

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

If brain commands fail with "cannot connect to database", the SSH tunnel
is probably down. Check it:
```bash
ps aux | grep 5433
```
The tunnel runs under user `zeresh` via the `openclaw-tunnel` systemd
service. It forwards port 5433 from bakkies (albanialink.com).

## Handling /brain invocations

When invoked as `/brain <args>`, run the brain command with those arguments:
```bash
brain --json <args>
```
If no arguments are given, run `brain --json stats` to show an overview.
