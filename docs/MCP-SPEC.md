# brain-mcp — MCP Server Specification

## What to Build

An MCP server (stdio transport) that wraps the brain-cli functionality for Claude Code sessions. It exposes brain read/write as MCP tools.

## Tech Stack

- **Language**: Python (same as brain-cli, can import directly)
- **MCP library**: `mcp` Python package (pip install mcp)
- **Transport**: stdio (Claude Code launches it as a subprocess)
- **File**: `/home/oc/projects/zeresh-brain/brain-mcp.py`

## Database Connection

Same as brain-cli:
- Host: `127.0.0.1`, Port: `5433`, User: `brain`, Password: `brain_local_only`
- Default database: `zeresh_brain`

## Tools to Expose

### Read Tools

1. **brain_search** — Hybrid semantic + keyword search
   - params: `query` (string, required), `kind` (string, optional), `project` (string, optional), `since` (string, optional), `limit` (int, default 10)
   - returns: retrieval_id + array of entries with id, kind, title, body, tags, project, relevance score

2. **brain_recent** — Latest entries
   - params: `kind` (optional), `project` (optional), `status` (optional), `since` (optional), `limit` (int, default 10)
   - returns: array of entries

3. **brain_get** — Get single entry by ID
   - params: `entry_id` (string, required)
   - returns: full entry

4. **brain_entity** — Get entity details
   - params: `slug` (string, required)
   - returns: entity with metadata

5. **brain_entities** — List all entities
   - returns: array of entities

6. **brain_events** — Upcoming/recent events
   - params: `from_date` (optional, default "today"), `to_date` (optional, default "+7 days")
   - returns: array of events

7. **brain_todos** — Open TODOs
   - params: `project` (optional)
   - returns: array of todo entries with status=active

8. **brain_where_is_jay** — Latest location
   - returns: location entry or "unknown"

9. **brain_context** — Project context dump
   - params: `project` (string, required)
   - returns: decisions + todos + entities + recent entries for that project

### Write Tools

10. **brain_remember** — Create new entry
    - params: `kind` (required), `title` (required), `body` (required), `source` (default "claude-code"), `project` (optional), `tags` (optional, comma-separated), `entity_refs` (optional), `status` (optional, default "active" for todos)
    - returns: new entry ID

11. **brain_update** — Update existing entry
    - params: `entry_id` (required), `status` (optional), `body` (optional), `title` (optional), `confidence` (optional)
    - returns: success/failure

12. **brain_boost** — Boost entries from a retrieval
    - params: `retrieval_id` (required), `positions` (required, comma-separated ints), `context` (optional)
    - returns: count boosted

13. **brain_forget** — Soft-delete an entry
    - params: `entry_id` (required)
    - returns: success/failure

## Implementation Approach

**Import from brain-cli.py** — don't duplicate the database logic. The MCP server should:
1. Import or call brain-cli functions directly
2. Or shell out to `brain --json <command>` if importing is complex (brain-cli outputs JSON with --json flag)

Option 2 (shelling out) is simpler and means brain-mcp.py stays thin. The `brain` CLI already handles all the DB logic, embeddings, boost scoring, etc. The MCP server just translates MCP tool calls to CLI invocations and parses JSON output.

## Claude Code Configuration

After building, create `.mcp.json` for project roots:

```json
{
  "mcpServers": {
    "brain": {
      "command": "python3",
      "args": ["/home/oc/projects/zeresh-brain/brain-mcp.py"]
    }
  }
}
```

## Testing

1. Run the server directly to check it starts: `python3 brain-mcp.py` (should wait for stdio input)
2. Test via Claude Code: create a test `.mcp.json`, start a Code session, verify tools appear
3. Test a search: ask Code to use brain_search

## Dependencies

Install if not present:
```bash
pip3 install --break-system-packages mcp
```

## Access Control Note

For now, all Claude Code sessions get full read/write. Access control (project-scoped writes) is a future TODO — the brain-cli `--source` flag tracks provenance, which is sufficient for now.
