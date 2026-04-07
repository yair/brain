# brain-mcp — MCP Server Specification

## What to Build

An MCP server (stdio transport) that wraps the brain CLI for MCP-compatible
clients. It exposes brain read/write operations as MCP tools.

## Tech Stack

- **Language**: Python
- **MCP library**: `mcp` Python package
- **Transport**: stdio (default) or SSE
- **File**: `brain-mcp.py`

## Database Connection

Inherits from environment variables (same as brain-cli):
- `BRAIN_DB_HOST`, `BRAIN_DB_PORT`, `BRAIN_DB_USER`, `BRAIN_DB_PASSWORD`

## Tools Exposed

### Read Tools

1. **brain_search** — Hybrid semantic + keyword search
2. **brain_recent** — Latest entries
3. **brain_get** — Single entry by ID
4. **brain_entity** — Entity by slug
5. **brain_entities** — List all entities
6. **brain_events** — Upcoming/recent events
7. **brain_todos** — Open TODOs
8. **brain_where** — Latest location
9. **brain_context** — Project context dump

### Write Tools

10. **brain_remember** — Create new entry
11. **brain_update** — Update existing entry
12. **brain_boost** — Boost useful search results
13. **brain_forget** — Soft-delete an entry

## Implementation

The MCP server shells out to `brain --json <command>` for each tool call.
This keeps brain-mcp.py thin — all DB logic, embeddings, and ranking live
in brain-cli.py.

## Client Configuration

Create `.mcp.json` in project roots:

```json
{
  "mcpServers": {
    "brain": {
      "command": "brain-mcp"
    }
  }
}
```

The `brain-mcp` wrapper must be on PATH (symlink to `~/.local/bin/`).

## Note on transport choice

The stdio transport works but has reliability issues during long sessions —
MCP connections can drop due to context compression or memory pressure.
For Claude Code, the CLI skill (`skill/SKILL.md`) is recommended instead,
as it's stateless and doesn't require a persistent connection.
