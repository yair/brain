#!/usr/bin/env python3
"""brain-mcp — MCP server wrapping the brain CLI.

Runs in two modes:
  - stdio (default): for local clients via .mcp.json {"command": ...}
  - sse:  persistent HTTP server for remote/resilient connections

The brain CLI must be on PATH. It connects to the brain DB using
BRAIN_DB_HOST / BRAIN_DB_PORT env vars (see .env.example).
"""

import atexit
import json
import logging
import os
import signal
import subprocess
import sys
from mcp.server.fastmcp import FastMCP

# Diagnostic logging — helps distinguish OOM kills from client disconnects
_log = logging.getLogger("brain-mcp")
_log_handler = logging.FileHandler("/tmp/brain-mcp.log")
_log_handler.setFormatter(logging.Formatter("%(asctime)s [%(process)d] %(message)s"))
_log.addHandler(_log_handler)
_log.setLevel(logging.INFO)

_log.info("brain-mcp starting (pid=%d, argv=%s)", os.getpid(), sys.argv)
atexit.register(lambda: _log.info("brain-mcp exiting normally via atexit"))
for _sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(_sig, lambda s, f: (_log.info("brain-mcp caught signal %s", s), sys.exit(0)))  # noqa: E501

MCP_PORT = int(os.environ.get("BRAIN_MCP_PORT", "8787"))
mcp = FastMCP("brain", port=MCP_PORT)

# Resolve brain CLI path: prefer BRAIN_CLI env, then script dir, then PATH
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BRAIN = os.environ.get("BRAIN_CLI", os.path.join(SCRIPT_DIR, "brain"))


def _run(args: list[str]) -> dict | list | str:
    """Run brain --json <args> and return parsed JSON."""
    cmd = [BRAIN, "--json"] + args
    _log.info("tool call: %s", " ".join(args))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        _log.warning("brain subprocess timed out: %s", " ".join(args))
        raise RuntimeError(f"brain command timed out after 30s: {' '.join(args)}")
    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip() or f"brain exited {result.returncode}"
        _log.warning("brain subprocess failed (rc=%d): %s", result.returncode, err[:200])
        raise RuntimeError(err)
    out = result.stdout.strip()
    if not out:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"message": out}


# ── Read Tools ──────────────────────────────────────────────────────


@mcp.tool()
def brain_search(
    query: str,
    kind: str = "",
    project: str = "",
    since: str = "",
    limit: int = 10,
) -> str:
    """Hybrid semantic + keyword search across brain entries.

    Returns entries ranked by relevance (semantic similarity + keyword match + boost score).
    """
    args = ["search", query, "--limit", str(limit)]
    if kind:
        args += ["--kind", kind]
    if project:
        args += ["--project", project]
    if since:
        args += ["--since", since]
    return json.dumps(_run(args))


@mcp.tool()
def brain_recent(
    kind: str = "",
    project: str = "",
    status: str = "",
    since: str = "",
    limit: int = 10,
) -> str:
    """Get the most recent brain entries, optionally filtered."""
    args = ["recent", "--limit", str(limit)]
    if kind:
        args += ["--kind", kind]
    if project:
        args += ["--project", project]
    if status:
        args += ["--status", status]
    if since:
        args += ["--since", since]
    return json.dumps(_run(args))


@mcp.tool()
def brain_get(entry_id: str) -> str:
    """Get a single brain entry by its UUID."""
    return json.dumps(_run(["get", entry_id]))


@mcp.tool()
def brain_entity(slug: str) -> str:
    """Get entity details by slug."""
    return json.dumps(_run(["entity", slug]))


@mcp.tool()
def brain_entities() -> str:
    """List all entities in the brain."""
    return json.dumps(_run(["entities"]))


@mcp.tool()
def brain_events(from_date: str = "", to_date: str = "") -> str:
    """Get upcoming/recent calendar events."""
    args = ["events"]
    if from_date:
        args += ["--from", from_date]
    if to_date:
        args += ["--to", to_date]
    return json.dumps(_run(args))


@mcp.tool()
def brain_todos(project: str = "") -> str:
    """Get open TODO entries."""
    args = ["todos"]
    if project:
        args += ["--project", project]
    return json.dumps(_run(args))


@mcp.tool()
def brain_where() -> str:
    """Get the latest known location."""
    return json.dumps(_run(["where"]))


@mcp.tool()
def brain_context(project: str) -> str:
    """Get full project context: decisions, todos, entities, and recent entries."""
    return json.dumps(_run(["context", project]))


# ── Write Tools ─────────────────────────────────────────────────────


@mcp.tool()
def brain_remember(
    kind: str,
    title: str,
    body: str,
    source: str = "claude-code",
    project: str = "",
    tags: str = "",
    entity_refs: str = "",
    status: str = "active",
) -> str:
    """Create a new brain entry (fact, decision, todo, insight, observation, preference, debrief)."""
    args = [
        "remember",
        "--kind", kind,
        "--title", title,
        "--body", body,
        "--source", source,
        "--status", status,
    ]
    if project:
        args += ["--project", project]
    if tags:
        args += ["--tags", tags]
    if entity_refs:
        args += ["--entity-refs", entity_refs]
    return json.dumps(_run(args))


@mcp.tool()
def brain_update(
    entry_id: str,
    status: str = "",
    body: str = "",
    title: str = "",
    confidence: float = -1,
) -> str:
    """Update an existing brain entry's status, body, title, or confidence."""
    args = ["update", entry_id]
    if status:
        args += ["--status", status]
    if body:
        args += ["--body", body]
    if title:
        args += ["--title", title]
    if confidence >= 0:
        args += ["--confidence", str(confidence)]
    return json.dumps(_run(args))


@mcp.tool()
def brain_boost(
    retrieval_id: str,
    positions: str,
    context: str = "",
) -> str:
    """Boost entries from a search retrieval that were useful. Improves future ranking.

    positions: comma-separated position numbers (e.g. "1,3,5") from the search results.
    """
    pos_list = [p.strip() for p in positions.split(",")]
    args = ["boost", "--retrieval", retrieval_id] + pos_list
    if context:
        args += ["--context", context]
    args += ["--source", "claude-code"]
    return json.dumps(_run(args))


@mcp.tool()
def brain_forget(entry_id: str) -> str:
    """Soft-delete a brain entry (sets expiry to now)."""
    return json.dumps(_run(["forget", entry_id]))


if __name__ == "__main__":
    import sys
    transport = sys.argv[1] if len(sys.argv) > 1 else "stdio"
    mcp.run(transport=transport)
