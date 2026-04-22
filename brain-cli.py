#!/usr/bin/env python3
"""brain-cli — CLI for the brain shared memory database."""

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone

import os

import click
import psycopg2
import psycopg2.extras
import requests

# Register UUID adapter
psycopg2.extras.register_uuid()


DB_DEFAULTS = {
    "host": os.environ.get("BRAIN_DB_HOST", "127.0.0.1"),
    "port": int(os.environ.get("BRAIN_DB_PORT", "5432")),
    "user": os.environ.get("BRAIN_CLI_DB_USER", ""),
    "password": os.environ.get("BRAIN_CLI_DB_PASSWORD", ""),
}

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-embedding-001:embedContent?key={GEMINI_API_KEY}"
)


def get_conn(db: str) -> psycopg2.extensions.connection:
    if not DB_DEFAULTS["user"] or not DB_DEFAULTS["password"]:
        click.echo(
            "Error: BRAIN_CLI_DB_USER and BRAIN_CLI_DB_PASSWORD must be set in .env. "
            "Brain CLI uses the brain_cli role (not the superuser brain role).",
            err=True,
        )
        sys.exit(1)
    try:
        return psycopg2.connect(dbname=db, **DB_DEFAULTS)
    except psycopg2.OperationalError as e:
        click.echo(f"Error: cannot connect to database '{db}': {e}", err=True)
        sys.exit(1)


def parse_since(since: str | None) -> datetime | None:
    if not since:
        return None
    now = datetime.now(timezone.utc)
    s = since.lower().strip()
    # Handle relative like "3 days ago", "1 week ago"
    for unit, delta in [
        ("week", timedelta(weeks=1)),
        ("day", timedelta(days=1)),
        ("hour", timedelta(hours=1)),
        ("month", timedelta(days=30)),
    ]:
        if unit in s:
            try:
                n = int("".join(c for c in s.split(unit)[0] if c.isdigit()) or "1")
            except ValueError:
                n = 1
            return now - delta * n
    # Try absolute parse
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    if s == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if s == "yesterday":
        return (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    click.echo(f"Error: cannot parse date '{since}'", err=True)
    sys.exit(2)


def parse_metadata(s: str | None) -> dict | None:
    """Parse a JSON object from the command line. Exit on invalid input."""
    if s is None:
        return None
    try:
        val = json.loads(s)
    except json.JSONDecodeError as e:
        click.echo(f"Error: --metadata is not valid JSON: {e}", err=True)
        sys.exit(2)
    if not isinstance(val, dict):
        click.echo("Error: --metadata must be a JSON object", err=True)
        sys.exit(2)
    return val


def generate_embedding(text: str) -> list[float] | None:
    """Generate a 768-dim embedding via Gemini gemini-embedding-001."""
    try:
        resp = requests.post(
            GEMINI_EMBED_URL,
            json={"content": {"parts": [{"text": text}]}, "outputDimensionality": 768},
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()["embedding"]["values"]
    except Exception as e:
        click.echo(f"Warning: embedding failed: {e}", err=True)
        return None


def format_entry(row: dict, quiet: bool = False, full: bool = False) -> str:
    if quiet:
        return row["title"]
    lines = []
    kind_colors = {
        "decision": "cyan",
        "todo": "yellow",
        "fact": "green",
        "insight": "magenta",
        "observation": "blue",
        "preference": "white",
        "debrief": "white",
    }
    kind = row.get("kind", "")
    color = kind_colors.get(kind, "white")
    status_str = ""
    if row.get("status") and row["status"] != "active":
        status_str = f" [{row['status']}]"
    lines.append(
        click.style(f"[{kind}]", fg=color)
        + f" {row['title']}{status_str}"
    )
    lines.append(click.style(f"  id: {row['id']}", dim=True))
    meta = []
    if row.get("project"):
        meta.append(f"project={row['project']}")
    if row.get("tags"):
        meta.append(f"tags={','.join(row['tags'])}")
    if row.get("source"):
        meta.append(f"source={row['source']}")
    if row.get("confidence") is not None and row["confidence"] != 1.0:
        meta.append(f"confidence={row['confidence']:.1f}")
    if meta:
        lines.append(click.style(f"  {' | '.join(meta)}", dim=True))
    ts = row.get("created_at")
    if ts:
        lines.append(click.style(f"  {ts:%Y-%m-%d %H:%M}", dim=True))
    if row.get("body"):
        body = row["body"]
        if not full and len(body) > 200:
            body = body[:200] + "..."
        lines.append(f"  {body}")
    return "\n".join(lines)


def format_entity(row: dict, quiet: bool = False, full: bool = False) -> str:
    if quiet:
        return f"{row['id']}: {row['name']}"
    deleted = " [deleted]" if row.get("deleted_at") else ""
    lines = [
        click.style(f"{row['id']}", fg="cyan", bold=True)
        + f" — {row['name']} ({row['kind']}){deleted}",
    ]
    if row.get("metadata"):
        for k, v in row["metadata"].items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def format_event(row: dict, quiet: bool = False, full: bool = False) -> str:
    if quiet:
        return f"{row['starts_at']:%Y-%m-%d %H:%M} {row['title']}"
    cancelled = " [cancelled]" if row.get("deleted_at") else ""
    lines = [
        click.style(f"{row['starts_at']:%Y-%m-%d %H:%M}", fg="yellow")
        + f" {row['title']}{cancelled}"
    ]
    if row.get("location"):
        lines.append(f"  Location: {row['location']}")
    if row.get("attendees"):
        lines.append(f"  Attendees: {', '.join(row['attendees'])}")
    if row.get("notes"):
        notes = row["notes"]
        if not full and len(notes) > 200:
            notes = notes[:200] + "..."
        lines.append(f"  {notes}")
    lines.append(click.style(f"  id: {row['id']}", dim=True))
    return "\n".join(lines)


def output_results(items: list, formatter, as_json: bool, quiet: bool, full: bool = False):
    if as_json:
        out = []
        for item in items:
            d = dict(item)
            for k, v in d.items():
                if isinstance(v, (datetime,)):
                    d[k] = v.isoformat()
                elif isinstance(v, uuid.UUID):
                    d[k] = str(v)
            d.pop("embedding", None)
            d.pop("tsv", None)
            out.append(d)
        click.echo(json.dumps(out, indent=2, default=str))
    else:
        if not items:
            click.echo("No results.")
            return
        for item in items:
            click.echo(formatter(item, quiet=quiet, full=full))
            click.echo()


# ── CLI ──────────────────────────────────────────────────────────────


@click.group()
@click.option("--db", default=os.environ.get("BRAIN_DB_NAME", "brain"),
              help="Database name (default: $BRAIN_DB_NAME or 'brain')")
@click.option("--json", "as_json", is_flag=True,
              help="Structured JSON output (for agents and scripts)")
@click.option("--quiet", is_flag=True,
              help="Minimal output: IDs on writes, titles on reads")
@click.option("--full", is_flag=True,
              help="Show full entry/event bodies (no 200-char truncation)")
@click.pass_context
def cli(ctx, db: str, as_json: bool, quiet: bool, full: bool):
    """brain — CLI for the brain shared memory database."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db
    ctx.obj["json"] = as_json
    ctx.obj["quiet"] = quiet
    ctx.obj["full"] = full


# ── stats ────────────────────────────────────────────────────────────


@cli.command()
@click.pass_context
def stats(ctx):
    """Show counts of entries by kind, entities, and events."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    cur.execute(
        "SELECT kind, count(*) FROM entries "
        "WHERE (expires_at IS NULL OR expires_at > now()) "
        "GROUP BY kind ORDER BY count DESC"
    )
    kinds = cur.fetchall()
    cur.execute("SELECT count(*) FROM entities WHERE deleted_at IS NULL")
    entity_count = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM events WHERE deleted_at IS NULL")
    event_count = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM entries WHERE embedding IS NOT NULL")
    embedded = cur.fetchone()[0]
    cur.execute(
        "SELECT count(*) FROM entries WHERE (expires_at IS NULL OR expires_at > now())"
    )
    total = cur.fetchone()[0]
    conn.close()

    if ctx.obj["json"]:
        click.echo(json.dumps({
            "entries": {k: c for k, c in kinds},
            "entries_total": total,
            "entries_with_embeddings": embedded,
            "entities": entity_count,
            "events": event_count,
        }, indent=2))
    else:
        click.echo(click.style("Entries:", bold=True))
        for kind, count in kinds:
            click.echo(f"  {kind}: {count}")
        click.echo(f"  total: {total} ({embedded} with embeddings)")
        click.echo(click.style(f"Entities: {entity_count}", bold=True))
        click.echo(click.style(f"Events: {event_count}", bold=True))


# ── recent ───────────────────────────────────────────────────────────


@cli.command()
@click.option("--kind", help="Filter by entry kind (decision, fact, todo, ...)")
@click.option("--status", help="Filter by status (active, superseded, expired, deleted)")
@click.option("--project", help="Filter by project slug")
@click.option("--source", help="Filter by source (who wrote it: claude-code, jay, ...)")
@click.option("--since", help="Entries since (e.g. '3 days ago', '2026-01-01')")
@click.option("--limit", default=10, help="Max results (default: 10)")
@click.pass_context
def recent(ctx, kind, status, project, source, since, limit):
    """Show recent entries, newest first."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    conditions = ["(expires_at IS NULL OR expires_at > now())"]
    params: list = []
    if kind:
        conditions.append("kind = %s")
        params.append(kind)
    if status:
        conditions.append("status = %s")
        params.append(status)
    if project:
        conditions.append("project = %s")
        params.append(project)
    if source:
        conditions.append("source = %s")
        params.append(source)
    since_dt = parse_since(since)
    if since_dt:
        conditions.append("created_at >= %s")
        params.append(since_dt)
    where = " AND ".join(conditions)
    params.append(limit)
    cur.execute(
        f"SELECT * FROM entries WHERE {where} ORDER BY created_at DESC LIMIT %s",
        params,
    )
    rows = cur.fetchall()
    conn.close()
    output_results(rows, format_entry, ctx.obj["json"], ctx.obj["quiet"], ctx.obj["full"])


# ── search ───────────────────────────────────────────────────────────


@cli.command()
@click.argument("query")
@click.option("--kind", help="Filter by entry kind")
@click.option("--project", help="Filter by project slug")
@click.option("--since", help="Entries since (e.g. '3 days ago')")
@click.option("--limit", default=10, help="Max results (default: 10)")
@click.pass_context
def search(ctx, query, kind, project, since, limit):
    """Hybrid semantic + keyword search (returns retrieval_id for boost)."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    conditions = ["(expires_at IS NULL OR expires_at > now())"]
    params: list = []

    if kind:
        conditions.append("kind = %s")
        params.append(kind)
    if project:
        conditions.append("project = %s")
        params.append(project)
    since_dt = parse_since(since)
    if since_dt:
        conditions.append("created_at >= %s")
        params.append(since_dt)

    where = " AND ".join(conditions)

    emb = generate_embedding(query)

    # Boost from access_log, time-decayed over 30 days
    boost_sub = """
        COALESCE((
            SELECT SUM(GREATEST(0, 1.0 - EXTRACT(EPOCH FROM (now() - accessed_at)) / (30*86400)))
            FROM access_log al WHERE al.entry_id = entries.id
        ), 0)
    """

    if emb:
        emb_str = "[" + ",".join(str(x) for x in emb) + "]"
        params_q = [emb_str, query] + params + [limit]
        cur.execute(
            f"""
            SELECT *,
                (0.7 * (1 - (embedding <=> %s::vector))) +
                (0.3 * COALESCE(ts_rank(tsv, plainto_tsquery('english', %s)), 0)) +
                (0.05 * {boost_sub})
                AS relevance
            FROM entries
            WHERE {where}
            ORDER BY relevance DESC NULLS LAST
            LIMIT %s
            """,
            params_q,
        )
    else:
        params_q = [query] + params + [query, limit]
        cur.execute(
            f"""
            SELECT *,
                ts_rank(tsv, plainto_tsquery('english', %s)) +
                (0.05 * {boost_sub})
                AS relevance
            FROM entries
            WHERE {where} AND tsv @@ plainto_tsquery('english', %s)
            ORDER BY relevance DESC
            LIMIT %s
            """,
            params_q,
        )

    rows = cur.fetchall()

    retrieval_id = None
    if rows:
        result_ids = [r["id"] for r in rows]
        cur2 = conn.cursor()
        cur2.execute(
            "INSERT INTO retrievals (query, result_ids, source) VALUES (%s, %s, %s) RETURNING id",
            [query, result_ids, ctx.obj.get("source", "cli")],
        )
        retrieval_id = cur2.fetchone()[0]
        conn.commit()

    conn.close()

    if ctx.obj["json"]:
        out = []
        for item in rows:
            d = dict(item)
            for k, v in d.items():
                if isinstance(v, (datetime,)):
                    d[k] = v.isoformat()
                elif isinstance(v, uuid.UUID):
                    d[k] = str(v)
            d.pop("embedding", None)
            d.pop("tsv", None)
            out.append(d)
        result = {"retrieval_id": str(retrieval_id) if retrieval_id else None, "results": out}
        click.echo(json.dumps(result, indent=2, default=str))
    else:
        if retrieval_id and not ctx.obj["quiet"]:
            click.echo(f"retrieval: {retrieval_id}")
            click.echo("")
        output_results(rows, format_entry, False, ctx.obj["quiet"], ctx.obj["full"])


# ── get ──────────────────────────────────────────────────────────────


@cli.command()
@click.argument("entry_id")
@click.pass_context
def get(ctx, entry_id):
    """Get a single entry by UUID."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM entries WHERE id = %s", [entry_id])
    row = cur.fetchone()
    conn.close()
    if not row:
        click.echo(f"Entry {entry_id} not found.", err=True)
        sys.exit(1)
    output_results([row], format_entry, ctx.obj["json"], ctx.obj["quiet"], ctx.obj["full"])


# ── entity / entities ────────────────────────────────────────────────


@cli.command()
@click.argument("slug")
@click.option("--include-deleted", is_flag=True, help="Show even if soft-deleted")
@click.pass_context
def entity(ctx, slug, include_deleted):
    """Get entity by slug."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if include_deleted:
        cur.execute("SELECT * FROM entities WHERE id = %s", [slug])
    else:
        cur.execute("SELECT * FROM entities WHERE id = %s AND deleted_at IS NULL", [slug])
    row = cur.fetchone()
    conn.close()
    if not row:
        click.echo(f"Entity '{slug}' not found.", err=True)
        sys.exit(1)
    output_results([row], format_entity, ctx.obj["json"], ctx.obj["quiet"], ctx.obj["full"])


@cli.command()
@click.option("--include-deleted", is_flag=True, help="Include soft-deleted entities")
@click.pass_context
def entities(ctx, include_deleted):
    """List all entities."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if include_deleted:
        cur.execute("SELECT * FROM entities ORDER BY kind, id")
    else:
        cur.execute("SELECT * FROM entities WHERE deleted_at IS NULL ORDER BY kind, id")
    rows = cur.fetchall()
    conn.close()
    output_results(rows, format_entity, ctx.obj["json"], ctx.obj["quiet"], ctx.obj["full"])


@cli.command("add-entity")
@click.option("--id", "slug", required=True, help="Slug (unique id, e.g. 'alice')")
@click.option("--kind", required=True, help="person, project, client, tool, place")
@click.option("--name", required=True, help="Display name")
@click.option("--metadata", help="JSON object of arbitrary metadata")
@click.pass_context
def add_entity(ctx, slug, kind, name, metadata):
    """Create a new entity (person, project, tool, place, ...)."""
    meta = parse_metadata(metadata) or {}
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO entities (id, kind, name, metadata) VALUES (%s, %s, %s, %s::jsonb)",
            [slug, kind, name, json.dumps(meta)],
        )
    except psycopg2.errors.UniqueViolation:
        click.echo(f"Entity '{slug}' already exists. Use update-entity to modify it.", err=True)
        sys.exit(1)
    conn.commit()
    conn.close()
    if ctx.obj["json"]:
        click.echo(json.dumps({"id": slug, "status": "created"}))
    elif ctx.obj["quiet"]:
        click.echo(slug)
    else:
        click.echo(f"Created entity {slug}: {name} ({kind})")


@cli.command("update-entity")
@click.argument("slug")
@click.option("--name", help="New display name")
@click.option("--kind", help="New kind")
@click.option("--metadata", help="Replace metadata entirely with this JSON object")
@click.option("--merge-metadata", help="Shallow-merge this JSON object into existing metadata")
@click.pass_context
def update_entity(ctx, slug, name, kind, metadata, merge_metadata):
    """Update an existing entity's fields or metadata."""
    if metadata and merge_metadata:
        click.echo("Error: pass --metadata OR --merge-metadata, not both", err=True)
        sys.exit(2)
    new_meta = parse_metadata(metadata)
    merge_meta = parse_metadata(merge_metadata)

    updates, params = [], []
    if name is not None:
        updates.append("name = %s")
        params.append(name)
    if kind is not None:
        updates.append("kind = %s")
        params.append(kind)
    if new_meta is not None:
        updates.append("metadata = %s::jsonb")
        params.append(json.dumps(new_meta))
    elif merge_meta is not None:
        updates.append("metadata = COALESCE(metadata, '{}'::jsonb) || %s::jsonb")
        params.append(json.dumps(merge_meta))

    if not updates:
        click.echo("Nothing to update.", err=True)
        sys.exit(2)

    params.append(slug)
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    cur.execute(
        f"UPDATE entities SET {', '.join(updates)} WHERE id = %s",
        params,
    )
    if cur.rowcount == 0:
        click.echo(f"Entity '{slug}' not found.", err=True)
        sys.exit(1)
    conn.commit()
    conn.close()
    if ctx.obj["json"]:
        click.echo(json.dumps({"id": slug, "status": "updated"}))
    elif ctx.obj["quiet"]:
        click.echo(slug)
    else:
        click.echo(f"Updated entity {slug}")


@cli.command("forget-entity")
@click.argument("slug")
@click.pass_context
def forget_entity(ctx, slug):
    """Soft-delete an entity (sets deleted_at). Entry references remain valid."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    cur.execute("UPDATE entities SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL",
                [slug])
    if cur.rowcount == 0:
        click.echo(f"Entity '{slug}' not found (or already deleted).", err=True)
        sys.exit(1)
    conn.commit()
    conn.close()
    if ctx.obj["json"]:
        click.echo(json.dumps({"id": slug, "status": "forgotten"}))
    elif ctx.obj["quiet"]:
        click.echo(slug)
    else:
        click.echo(f"Forgotten entity {slug}")


# ── events ───────────────────────────────────────────────────────────


@cli.command()
@click.option("--from", "from_date", help="Start date (default: today)")
@click.option("--to", "to_date", help="End date (default: 7 days from now)")
@click.option("--include-deleted", is_flag=True, help="Include cancelled events")
@click.pass_context
def events(ctx, from_date, to_date, include_deleted):
    """Show events in a date range (default: next 7 days)."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    start = (parse_since(from_date) if from_date
             else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0))
    end = parse_since(to_date) if to_date else start + timedelta(days=7)
    deleted_clause = "" if include_deleted else " AND deleted_at IS NULL"
    cur.execute(
        f"SELECT * FROM events WHERE starts_at >= %s AND starts_at <= %s{deleted_clause} "
        "ORDER BY starts_at",
        [start, end],
    )
    rows = cur.fetchall()
    conn.close()
    output_results(rows, format_event, ctx.obj["json"], ctx.obj["quiet"], ctx.obj["full"])


@cli.command("add-event")
@click.option("--title", required=True)
@click.option("--starts-at", required=True, help="Start time (e.g. '2026-03-10 09:00')")
@click.option("--ends-at", help="End time")
@click.option("--location", "loc", help="Location")
@click.option("--attendees", help="Comma-separated attendees")
@click.option("--notes", help="Notes")
@click.option("--source", default="manual", help="Source identifier")
@click.pass_context
def add_event(ctx, title, starts_at, ends_at, loc, attendees, notes, source):
    """Add a calendar event."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    attendee_list = [a.strip() for a in attendees.split(",")] if attendees else []
    cur.execute(
        "INSERT INTO events (title, starts_at, ends_at, location, attendees, notes, source) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        [title, starts_at, ends_at, loc, attendee_list, notes, source],
    )
    event_id = cur.fetchone()[0]
    conn.commit()
    conn.close()

    if ctx.obj["json"]:
        click.echo(json.dumps({"id": str(event_id), "status": "created"}))
    elif ctx.obj["quiet"]:
        click.echo(str(event_id))
    else:
        click.echo(f"Added event {event_id}: {title}")


@cli.command("update-event")
@click.argument("event_id")
@click.option("--title", help="New title")
@click.option("--starts-at", help="New start time")
@click.option("--ends-at", help="New end time")
@click.option("--location", "loc", help="New location")
@click.option("--attendees", help="New comma-separated attendees (replaces existing)")
@click.option("--notes", help="New notes")
@click.pass_context
def update_event(ctx, event_id, title, starts_at, ends_at, loc, attendees, notes):
    """Update an event's fields."""
    updates, params = [], []
    if title is not None:
        updates.append("title = %s")
        params.append(title)
    if starts_at is not None:
        updates.append("starts_at = %s")
        params.append(starts_at)
    if ends_at is not None:
        updates.append("ends_at = %s")
        params.append(ends_at)
    if loc is not None:
        updates.append("location = %s")
        params.append(loc)
    if attendees is not None:
        updates.append("attendees = %s")
        params.append([a.strip() for a in attendees.split(",")] if attendees else [])
    if notes is not None:
        updates.append("notes = %s")
        params.append(notes)

    if not updates:
        click.echo("Nothing to update.", err=True)
        sys.exit(2)

    params.append(event_id)
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    cur.execute(
        f"UPDATE events SET {', '.join(updates)} WHERE id = %s",
        params,
    )
    if cur.rowcount == 0:
        click.echo(f"Event {event_id} not found.", err=True)
        sys.exit(1)
    conn.commit()
    conn.close()
    if ctx.obj["json"]:
        click.echo(json.dumps({"id": event_id, "status": "updated"}))
    elif ctx.obj["quiet"]:
        click.echo(event_id)
    else:
        click.echo(f"Updated event {event_id}")


@cli.command("cancel-event")
@click.argument("event_id")
@click.pass_context
def cancel_event(ctx, event_id):
    """Soft-delete / cancel an event (sets deleted_at)."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    cur.execute("UPDATE events SET deleted_at = now() WHERE id = %s AND deleted_at IS NULL",
                [event_id])
    if cur.rowcount == 0:
        click.echo(f"Event {event_id} not found (or already cancelled).", err=True)
        sys.exit(1)
    conn.commit()
    conn.close()
    if ctx.obj["json"]:
        click.echo(json.dumps({"id": event_id, "status": "cancelled"}))
    elif ctx.obj["quiet"]:
        click.echo(event_id)
    else:
        click.echo(f"Cancelled event {event_id}")


# ── todos ────────────────────────────────────────────────────────────


@cli.command()
@click.option("--project", help="Filter by project slug")
@click.pass_context
def todos(ctx, project):
    """Show open TODOs (shortcut for: recent --kind todo --status active)."""
    ctx.invoke(recent, kind="todo", status="active", project=project,
               source=None, since=None, limit=50)


# ── where (location) ─────────────────────────────────────────────────


@cli.command("where")
@click.pass_context
def where(ctx):
    """Show the latest known location."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM location ORDER BY timestamp DESC LIMIT 1")
    row = cur.fetchone()
    conn.close()
    if not row:
        click.echo("No location data.")
        return
    if ctx.obj["json"]:
        d = dict(row)
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        click.echo(json.dumps(d, indent=2, default=str))
    else:
        label = row.get("label") or "unknown"
        click.echo(f"{label} ({row.get('lat')}, {row.get('lon')}) — "
                   f"{row.get('source')} @ {row.get('timestamp')}")


# ── context ──────────────────────────────────────────────────────────


@cli.command()
@click.argument("project")
@click.pass_context
def context(ctx, project):
    """Dump decisions, open TODOs, entities, and recent entries for a project."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    sections = {}

    cur.execute(
        "SELECT * FROM entries WHERE project = %s AND kind = 'decision' "
        "AND (expires_at IS NULL OR expires_at > now()) "
        "ORDER BY created_at DESC LIMIT 10",
        [project],
    )
    sections["decisions"] = cur.fetchall()

    cur.execute(
        "SELECT * FROM entries WHERE project = %s AND kind = 'todo' AND status = 'active' "
        "AND (expires_at IS NULL OR expires_at > now()) ORDER BY created_at DESC",
        [project],
    )
    sections["todos"] = cur.fetchall()

    cur.execute(
        "SELECT * FROM entities WHERE deleted_at IS NULL AND (id = %s OR %s = ANY("
        "  SELECT unnest(entity_refs) FROM entries WHERE project = %s"
        "))",
        [project, project, project],
    )
    sections["entities"] = cur.fetchall()

    cur.execute(
        "SELECT * FROM entries WHERE project = %s "
        "AND (expires_at IS NULL OR expires_at > now()) "
        "ORDER BY created_at DESC LIMIT 10",
        [project],
    )
    sections["recent"] = cur.fetchall()

    conn.close()

    if ctx.obj["json"]:
        out = {}
        for section, rows in sections.items():
            out[section] = []
            for row in rows:
                d = dict(row)
                for k, v in d.items():
                    if isinstance(v, (datetime,)):
                        d[k] = v.isoformat()
                    elif isinstance(v, uuid.UUID):
                        d[k] = str(v)
                d.pop("embedding", None)
                d.pop("tsv", None)
                out[section].append(d)
        click.echo(json.dumps(out, indent=2, default=str))
    else:
        for section, rows in sections.items():
            click.echo(click.style(f"\n── {section.upper()} ──", bold=True))
            if not rows:
                click.echo("  (none)")
            formatter = format_entity if section == "entities" else format_entry
            for row in rows:
                click.echo(formatter(row, quiet=ctx.obj["quiet"], full=ctx.obj["full"]))
                click.echo()


# ── remember ─────────────────────────────────────────────────────────


@cli.command()
@click.option("--kind", required=True, help="Entry kind (decision, fact, todo, insight, observation, preference, debrief)")
@click.option("--title", required=True, help="Short title")
@click.option("--body", required=True, help="Full content")
@click.option("--source", default="cli", help="Source identifier (default: 'cli')")
@click.option("--project", help="Project slug")
@click.option("--tags", help="Comma-separated tags")
@click.option("--entity-refs", help="Comma-separated entity slugs referenced by this entry")
@click.option("--status", default="active", help="Status (default: active)")
@click.option("--confidence", type=float, default=1.0, help="Confidence 0..1 (default: 1.0)")
@click.pass_context
def remember(ctx, kind, title, body, source, project, tags, entity_refs, status, confidence):
    """Create a new entry. Auto-generates an embedding via Gemini."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()

    tag_list = [t.strip() for t in tags.split(",")] if tags else []
    ref_list = [r.strip() for r in entity_refs.split(",")] if entity_refs else []

    embed_text = f"{title} {body}"
    emb = generate_embedding(embed_text)

    cur.execute(
        """
        INSERT INTO entries (kind, source, title, body, tags, project, entity_refs, embedding, status, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        [kind, source, title, body, tag_list, project, ref_list,
         "[" + ",".join(str(x) for x in emb) + "]" if emb else None,
         status, confidence],
    )
    new_id = cur.fetchone()[0]
    conn.commit()
    conn.close()

    if ctx.obj["json"]:
        click.echo(json.dumps({"id": str(new_id), "kind": kind, "title": title}))
    elif ctx.obj["quiet"]:
        click.echo(str(new_id))
    else:
        click.echo(f"Created {kind}: {new_id}")


# ── update ───────────────────────────────────────────────────────────


@cli.command()
@click.argument("entry_id")
@click.option("--status", help="New status (active, superseded, expired, deleted)")
@click.option("--body", help="New body (does NOT regenerate embedding; use 'embed' after)")
@click.option("--confidence", type=float, help="New confidence 0..1")
@click.option("--title", help="New title")
@click.pass_context
def update(ctx, entry_id, status, body, confidence, title):
    """Update an existing entry."""
    updates = {}
    if status is not None:
        updates["status"] = status
    if body is not None:
        updates["body"] = body
    if confidence is not None:
        updates["confidence"] = confidence
    if title is not None:
        updates["title"] = title

    if not updates:
        click.echo("Nothing to update.", err=True)
        sys.exit(2)

    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()

    set_clauses = []
    params = []
    for col, val in updates.items():
        set_clauses.append(f"{col} = %s")
        params.append(val)

    params.append(entry_id)
    cur.execute(
        f"UPDATE entries SET {', '.join(set_clauses)} WHERE id = %s",
        params,
    )
    if cur.rowcount == 0:
        click.echo(f"Entry {entry_id} not found.", err=True)
        sys.exit(1)
    conn.commit()
    conn.close()

    if ctx.obj["json"]:
        click.echo(json.dumps({"id": str(entry_id), "status": "updated"}))
    elif ctx.obj["quiet"]:
        click.echo(entry_id)
    else:
        click.echo(f"Updated {entry_id}")


# ── supersede ────────────────────────────────────────────────────────


@cli.command()
@click.argument("old_id")
@click.option("--title", required=True, help="New entry title")
@click.option("--body", required=True, help="New entry body")
@click.option("--source", default="cli", help="Source identifier")
@click.pass_context
def supersede(ctx, old_id, title, body, source):
    """Replace an entry with a new one. Old entry gets superseded_by set."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM entries WHERE id = %s", [old_id])
    old = cur.fetchone()
    if not old:
        click.echo(f"Entry {old_id} not found.", err=True)
        sys.exit(1)

    emb = generate_embedding(f"{title} {body}")

    cur.execute(
        """
        INSERT INTO entries (kind, source, title, body, tags, project, entity_refs, embedding, status, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        [old["kind"], source, title, body, old["tags"], old["project"], old["entity_refs"],
         "[" + ",".join(str(x) for x in emb) + "]" if emb else None,
         old["status"], 1.0],
    )
    new_id = cur.fetchone()["id"]

    cur.execute("UPDATE entries SET superseded_by = %s WHERE id = %s", [new_id, old_id])
    conn.commit()
    conn.close()

    if ctx.obj["json"]:
        click.echo(json.dumps({"old_id": str(old_id), "new_id": str(new_id),
                               "status": "superseded"}))
    elif ctx.obj["quiet"]:
        click.echo(str(new_id))
    else:
        click.echo(f"Superseded {old_id} → {new_id}")


# ── forget ───────────────────────────────────────────────────────────


@cli.command()
@click.argument("entry_id")
@click.pass_context
def forget(ctx, entry_id):
    """Soft-delete an entry (sets expires_at to now)."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    cur.execute("UPDATE entries SET expires_at = now() WHERE id = %s", [entry_id])
    if cur.rowcount == 0:
        click.echo(f"Entry {entry_id} not found.", err=True)
        sys.exit(1)
    conn.commit()
    conn.close()

    if ctx.obj["json"]:
        click.echo(json.dumps({"id": str(entry_id), "status": "forgotten"}))
    elif ctx.obj["quiet"]:
        click.echo(entry_id)
    else:
        click.echo(f"Forgotten {entry_id}")


# ── log-location ─────────────────────────────────────────────────────


@cli.command("log-location")
@click.option("--lat", required=True, type=float, help="Latitude")
@click.option("--lon", required=True, type=float, help="Longitude")
@click.option("--label", help="Human-readable label (e.g. 'office')")
@click.option("--source", default="manual", help="Source identifier (default: 'manual')")
@click.option("--accuracy", type=float, help="Accuracy in meters")
@click.pass_context
def log_location(ctx, lat, lon, label, source, accuracy):
    """Log a GPS/presence point into the location hypertable."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO location (timestamp, source, lat, lon, accuracy_m, label) "
        "VALUES (now(), %s, %s, %s, %s, %s)",
        [source, lat, lon, accuracy, label],
    )
    conn.commit()
    conn.close()

    if ctx.obj["json"]:
        click.echo(json.dumps({"status": "logged", "label": label}))
    elif ctx.obj["quiet"]:
        click.echo(label or "logged")
    else:
        click.echo(f"Logged location: {label or 'unlabeled'} ({lat}, {lon})")


# ── embed ────────────────────────────────────────────────────────────


@cli.command()
@click.argument("entry_id", required=False)
@click.option("--all", "embed_all", is_flag=True, help="Embed all entries")
@click.option("--missing", is_flag=True, help="Only entries missing embeddings (use with --all)")
@click.pass_context
def embed(ctx, entry_id, embed_all, missing):
    """Generate/update embeddings for one entry, or backfill --all [--missing]."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if entry_id:
        cur.execute("SELECT id, title, body FROM entries WHERE id = %s", [entry_id])
        rows = cur.fetchall()
    elif embed_all:
        if missing:
            cur.execute("SELECT id, title, body FROM entries WHERE embedding IS NULL")
        else:
            cur.execute("SELECT id, title, body FROM entries")
        rows = cur.fetchall()
    else:
        click.echo("Provide an entry ID or use --all [--missing]", err=True)
        sys.exit(2)

    count = 0
    for row in rows:
        emb = generate_embedding(f"{row['title']} {row['body']}")
        if emb:
            cur.execute(
                "UPDATE entries SET embedding = %s WHERE id = %s",
                ["[" + ",".join(str(x) for x in emb) + "]", row["id"]],
            )
            count += 1

    conn.commit()
    conn.close()
    click.echo(f"Embedded {count}/{len(rows)} entries")


# ── boost / boost-history ────────────────────────────────────────────


@cli.command()
@click.argument("entry_ids", nargs=-1, required=True)
@click.option("--retrieval", "-r", default=None,
              help="Retrieval ID: lets you pass positions (1, 2, 3...) instead of UUIDs")
@click.option("--context", "ctx_text", default=None,
              help="What query/task made these entries useful")
@click.option("--kind", default="boost", help="Access kind: boost, cited, acted_on")
@click.option("--source", default="cli", help="Who is boosting (default: 'cli')")
@click.pass_context
def boost(ctx, entry_ids, retrieval, ctx_text, kind, source):
    """Boost entries that were useful (improves their ranking in future searches).

    Usage:
      brain boost --retrieval <rid> 3 7      (by position in that retrieval)
      brain boost <uuid> [<uuid> ...]         (by entry ID directly)

    Position numbers require --retrieval to avoid race conditions.
    Boosts apply to individual ENTRIES, not to the retrieval as a whole.
    """
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()

    positions = {}
    if retrieval:
        cur.execute("SELECT result_ids FROM retrievals WHERE id::text LIKE %s", [retrieval + "%"])
        row = cur.fetchone()
        if row:
            positions = {str(i + 1): str(uid) for i, uid in enumerate(row[0])}
        else:
            click.echo(f"Retrieval not found: {retrieval}", err=True)
            sys.exit(1)

    boosted = 0
    for eid in entry_ids:
        if eid.isdigit() and eid in positions:
            eid = positions[eid]
        elif eid.isdigit() and not positions:
            click.echo(f"Position '{eid}' requires --retrieval <id>. "
                       "Use entry UUIDs directly, or pass -r.", err=True)
            continue

        cur.execute("SELECT id, title FROM entries WHERE id::text LIKE %s", [eid + "%"])
        row = cur.fetchone()
        if not row:
            click.echo(f"Entry not found: {eid}", err=True)
            continue
        full_id = row[0]
        cur.execute(
            "INSERT INTO access_log (entry_id, kind, context, session_id) VALUES (%s, %s, %s, %s)",
            [full_id, kind, ctx_text, source],
        )
        boosted += 1
        if not ctx.obj["quiet"]:
            click.echo(f"Boosted: {row[1]} ({full_id})")

    conn.commit()
    conn.close()
    if ctx.obj["json"]:
        click.echo(json.dumps({"boosted": boosted}))
    elif ctx.obj["quiet"]:
        click.echo(str(boosted))


@cli.command("boost-history")
@click.argument("entry_id", required=False)
@click.option("--limit", default=20, help="Max results (default: 20)")
@click.pass_context
def boost_history(ctx, entry_id, limit):
    """Show boost/access history. With an entry ID: per-entry log; without: most-boosted."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    if entry_id:
        cur.execute(
            """SELECT al.*, e.title FROM access_log al
               JOIN entries e ON e.id = al.entry_id
               WHERE al.entry_id::text LIKE %s
               ORDER BY al.accessed_at DESC LIMIT %s""",
            [entry_id + "%", limit],
        )
    else:
        cur.execute(
            """SELECT e.id, e.title, e.kind,
                      count(al.id) as boost_count,
                      max(al.accessed_at) as last_boosted
               FROM access_log al
               JOIN entries e ON e.id = al.entry_id
               GROUP BY e.id, e.title, e.kind
               ORDER BY boost_count DESC
               LIMIT %s""",
            [limit],
        )

    rows = cur.fetchall()
    conn.close()

    if ctx.obj["json"]:
        click.echo(json.dumps([dict(r) for r in rows], default=str))
        return

    if not rows:
        click.echo("No boost history yet.")
        return

    if entry_id:
        for r in rows:
            click.echo(f"  {r['accessed_at']:%Y-%m-%d %H:%M} | {r['kind']} | "
                       f"{r.get('context', '-')}")
    else:
        for r in rows:
            click.echo(f"  [{r['kind']}] {r['title']} — {r['boost_count']} boosts "
                       f"(last: {r['last_boosted']:%Y-%m-%d %H:%M})")


if __name__ == "__main__":
    cli()
