#!/usr/bin/env python3
"""brain-cli — CLI for the zeresh-brain shared memory database."""

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone

import click
import psycopg2
import psycopg2.extras
import requests

# Register UUID adapter
psycopg2.extras.register_uuid()

DB_DEFAULTS = {
    "host": "127.0.0.1",
    "port": 5433,
    "user": "brain",
    "password": "brain_local_only",
}

GEMINI_API_KEY = "AIzaSyAYkIwuj_GbD1Vx63hkWT0jleiXAXJTuSQ"
GEMINI_EMBED_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-embedding-001:embedContent?key={GEMINI_API_KEY}"
)


def get_conn(db: str) -> psycopg2.extensions.connection:
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


def format_entry(row: dict, quiet: bool = False) -> str:
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
        if len(body) > 200:
            body = body[:200] + "..."
        lines.append(f"  {body}")
    return "\n".join(lines)


def format_entity(row: dict, quiet: bool = False) -> str:
    if quiet:
        return f"{row['id']}: {row['name']}"
    lines = [
        click.style(f"{row['id']}", fg="cyan", bold=True) + f" — {row['name']} ({row['kind']})",
    ]
    if row.get("metadata"):
        for k, v in row["metadata"].items():
            lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def format_event(row: dict, quiet: bool = False) -> str:
    if quiet:
        return f"{row['starts_at']:%Y-%m-%d %H:%M} {row['title']}"
    lines = [
        click.style(f"{row['starts_at']:%Y-%m-%d %H:%M}", fg="yellow")
        + f" {row['title']}"
    ]
    if row.get("location"):
        lines.append(f"  Location: {row['location']}")
    if row.get("attendees"):
        lines.append(f"  Attendees: {', '.join(row['attendees'])}")
    if row.get("notes"):
        lines.append(f"  {row['notes']}")
    lines.append(click.style(f"  id: {row['id']}", dim=True))
    return "\n".join(lines)


def output_results(items: list, formatter, as_json: bool, quiet: bool):
    if as_json:
        # Convert to serializable dicts
        out = []
        for item in items:
            d = dict(item)
            for k, v in d.items():
                if isinstance(v, (datetime,)):
                    d[k] = v.isoformat()
                elif isinstance(v, uuid.UUID):
                    d[k] = str(v)
            # Drop embedding from output (too large)
            d.pop("embedding", None)
            d.pop("tsv", None)
            out.append(d)
        click.echo(json.dumps(out, indent=2, default=str))
    else:
        if not items:
            click.echo("No results.")
            return
        for item in items:
            click.echo(formatter(item, quiet=quiet))
            click.echo()


# ── CLI ──────────────────────────────────────────────────────────────


@click.group()
@click.option("--db", default="zeresh_brain", help="Database name")
@click.option("--json", "as_json", is_flag=True, help="JSON output")
@click.option("--quiet", is_flag=True, help="Minimal output")
@click.pass_context
def cli(ctx, db: str, as_json: bool, quiet: bool):
    """brain — CLI for the zeresh-brain shared memory database."""
    ctx.ensure_object(dict)
    ctx.obj["db"] = db
    ctx.obj["json"] = as_json
    ctx.obj["quiet"] = quiet


# ── stats ────────────────────────────────────────────────────────────


@cli.command()
@click.pass_context
def stats(ctx):
    """Show counts of entries by kind, entities, and events."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    cur.execute("SELECT kind, count(*) FROM entries WHERE (expires_at IS NULL OR expires_at > now()) GROUP BY kind ORDER BY count DESC")
    kinds = cur.fetchall()
    cur.execute("SELECT count(*) FROM entities")
    entity_count = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM events")
    event_count = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM entries WHERE embedding IS NOT NULL")
    embedded = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM entries WHERE (expires_at IS NULL OR expires_at > now())")
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
@click.option("--kind", help="Filter by entry kind")
@click.option("--status", help="Filter by status")
@click.option("--project", help="Filter by project")
@click.option("--since", help="Entries since (e.g. '3 days ago', '2026-01-01')")
@click.option("--limit", default=10, help="Max results")
@click.pass_context
def recent(ctx, kind, status, project, since, limit):
    """Show recent entries."""
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
    output_results(rows, format_entry, ctx.obj["json"], ctx.obj["quiet"])


# ── search ───────────────────────────────────────────────────────────


@cli.command()
@click.argument("query")
@click.option("--kind", help="Filter by entry kind")
@click.option("--project", help="Filter by project")
@click.option("--since", help="Entries since")
@click.option("--limit", default=10, help="Max results")
@click.pass_context
def search(ctx, query, kind, project, since, limit):
    """Hybrid semantic + keyword search."""
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

    # Try to get embedding for semantic search
    emb = generate_embedding(query)

    # Boost subquery: count recent boosts with time decay
    # Each boost adds score, but decays over 30 days (boost from today = 1.0, 30 days ago = 0.0)
    boost_sub = """
        COALESCE((
            SELECT SUM(GREATEST(0, 1.0 - EXTRACT(EPOCH FROM (now() - accessed_at)) / (30*86400)))
            FROM access_log al WHERE al.entry_id = entries.id
        ), 0)
    """

    if emb:
        emb_str = "[" + ",".join(str(x) for x in emb) + "]"
        params_q = params + [emb_str, query, limit]
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
        # Fallback: keyword only
        params_q = params + [query, query, limit]
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

    # Record retrieval for boost referencing
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

    # Show retrieval ID in output
    if ctx.obj["json"]:
        # In JSON mode, wrap results with retrieval_id
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
        output_results(rows, format_entry, False, ctx.obj["quiet"])


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
    output_results([row], format_entry, ctx.obj["json"], ctx.obj["quiet"])


# ── entity / entities ────────────────────────────────────────────────


@cli.command()
@click.argument("slug")
@click.pass_context
def entity(ctx, slug):
    """Get entity by slug."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM entities WHERE id = %s", [slug])
    row = cur.fetchone()
    conn.close()
    if not row:
        click.echo(f"Entity '{slug}' not found.", err=True)
        sys.exit(1)
    output_results([row], format_entity, ctx.obj["json"], ctx.obj["quiet"])


@cli.command()
@click.pass_context
def entities(ctx):
    """List all entities."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM entities ORDER BY kind, id")
    rows = cur.fetchall()
    conn.close()
    output_results(rows, format_entity, ctx.obj["json"], ctx.obj["quiet"])


# ── events ───────────────────────────────────────────────────────────


@cli.command()
@click.option("--from", "from_date", help="Start date (default: today)")
@click.option("--to", "to_date", help="End date (default: 7 days from now)")
@click.pass_context
def events(ctx, from_date, to_date):
    """Show upcoming events."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    start = parse_since(from_date) if from_date else datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    end = parse_since(to_date) if to_date else start + timedelta(days=7)
    cur.execute(
        "SELECT * FROM events WHERE starts_at >= %s AND starts_at <= %s ORDER BY starts_at",
        [start, end],
    )
    rows = cur.fetchall()
    conn.close()
    output_results(rows, format_event, ctx.obj["json"], ctx.obj["quiet"])


# ── todos ────────────────────────────────────────────────────────────


@cli.command()
@click.option("--project", help="Filter by project")
@click.pass_context
def todos(ctx, project):
    """Show open TODOs."""
    ctx.invoke(recent, kind="todo", status="active", project=project, since=None, limit=50)


# ── where-is-jay ─────────────────────────────────────────────────────


@cli.command("where-is-jay")
@click.pass_context
def where_is_jay(ctx):
    """Show Jay's latest location."""
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
        click.echo(f"{label} ({row.get('lat')}, {row.get('lon')}) — {row.get('source')} @ {row.get('timestamp')}")


# ── context ──────────────────────────────────────────────────────────


@cli.command()
@click.argument("project")
@click.pass_context
def context(ctx, project):
    """Dump recent decisions, open TODOs, and entities for a project."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    sections = {}

    # Recent decisions
    cur.execute(
        "SELECT * FROM entries WHERE project = %s AND kind = 'decision' AND (expires_at IS NULL OR expires_at > now()) ORDER BY created_at DESC LIMIT 10",
        [project],
    )
    sections["decisions"] = cur.fetchall()

    # Open TODOs
    cur.execute(
        "SELECT * FROM entries WHERE project = %s AND kind = 'todo' AND status = 'active' AND (expires_at IS NULL OR expires_at > now()) ORDER BY created_at DESC",
        [project],
    )
    sections["todos"] = cur.fetchall()

    # Related entities
    cur.execute(
        "SELECT * FROM entities WHERE id = %s OR %s = ANY(SELECT unnest(entity_refs) FROM entries WHERE project = %s)",
        [project, project, project],
    )
    sections["entities"] = cur.fetchall()

    # Recent entries (all kinds)
    cur.execute(
        "SELECT * FROM entries WHERE project = %s AND (expires_at IS NULL OR expires_at > now()) ORDER BY created_at DESC LIMIT 10",
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
                click.echo(formatter(row, quiet=ctx.obj["quiet"]))
                click.echo()


# ── remember ─────────────────────────────────────────────────────────


@cli.command()
@click.option("--kind", required=True, help="Entry kind (decision, fact, todo, etc.)")
@click.option("--title", required=True, help="Short title")
@click.option("--body", required=True, help="Full content")
@click.option("--source", default="cli", help="Source identifier")
@click.option("--project", help="Project slug")
@click.option("--tags", help="Comma-separated tags")
@click.option("--entity-refs", help="Comma-separated entity refs")
@click.option("--status", default="active", help="Status (default: active)")
@click.option("--confidence", type=float, default=1.0, help="Confidence 0-1")
@click.pass_context
def remember(ctx, kind, title, body, source, project, tags, entity_refs, status, confidence):
    """Create a new entry."""
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
@click.option("--status", help="New status")
@click.option("--body", help="New body")
@click.option("--confidence", type=float, help="New confidence")
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

    # tsv is a generated column — no manual update needed

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
    """Replace an entry with a new one."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Get old entry for kind/project/tags
    cur.execute("SELECT * FROM entries WHERE id = %s", [old_id])
    old = cur.fetchone()
    if not old:
        click.echo(f"Entry {old_id} not found.", err=True)
        sys.exit(1)

    emb = generate_embedding(f"{title} {body}")

    cur.execute(
        """
        INSERT INTO entries (kind, source, title, body, tags, project, entity_refs, embedding, tsv, status, confidence)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, to_tsvector('english', %s || ' ' || %s), %s, %s)
        RETURNING id
        """,
        [old["kind"], source, title, body, old["tags"], old["project"], old["entity_refs"],
         "[" + ",".join(str(x) for x in emb) + "]" if emb else None,
         title, body, old["status"], 1.0],
    )
    new_id = cur.fetchone()["id"]

    cur.execute("UPDATE entries SET superseded_by = %s WHERE id = %s", [new_id, old_id])
    conn.commit()
    conn.close()

    if ctx.obj["quiet"]:
        click.echo(str(new_id))
    else:
        click.echo(f"Superseded {old_id} → {new_id}")


# ── forget ───────────────────────────────────────────────────────────


@cli.command()
@click.argument("entry_id")
@click.pass_context
def forget(ctx, entry_id):
    """Soft-delete an entry (set expires_at to now)."""
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
@click.option("--lat", required=True, type=float)
@click.option("--lon", required=True, type=float)
@click.option("--label", help="Location label")
@click.option("--source", default="manual", help="Source")
@click.option("--accuracy", type=float, help="Accuracy in meters")
@click.pass_context
def log_location(ctx, lat, lon, label, source, accuracy):
    """Log a location entry."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO location (source, lat, lon, accuracy_m, label) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        [source, lat, lon, accuracy, label],
    )
    loc_id = cur.fetchone()[0]
    conn.commit()
    conn.close()

    if ctx.obj["quiet"]:
        click.echo(str(loc_id))
    else:
        click.echo(f"Logged location {loc_id}: {label or 'unlabeled'} ({lat}, {lon})")


# ── add-event ────────────────────────────────────────────────────────


@cli.command("add-event")
@click.option("--title", required=True)
@click.option("--starts-at", required=True, help="Start time (e.g. '2026-03-10 09:00')")
@click.option("--ends-at", help="End time")
@click.option("--location", "loc", help="Location")
@click.option("--attendees", help="Comma-separated attendees")
@click.option("--notes", help="Notes")
@click.option("--source", default="manual")
@click.pass_context
def add_event(ctx, title, starts_at, ends_at, loc, attendees, notes, source):
    """Add a calendar event."""
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()
    attendee_list = [a.strip() for a in attendees.split(",")] if attendees else []
    cur.execute(
        "INSERT INTO events (title, starts_at, ends_at, location, attendees, notes, source) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
        [title, starts_at, ends_at, loc, attendee_list, notes, source],
    )
    event_id = cur.fetchone()[0]
    conn.commit()
    conn.close()

    if ctx.obj["quiet"]:
        click.echo(str(event_id))
    else:
        click.echo(f"Added event {event_id}: {title}")


# ── embed ────────────────────────────────────────────────────────────


@cli.command()
@click.argument("entry_id", required=False)
@click.option("--all", "embed_all", is_flag=True, help="Embed all entries")
@click.option("--missing", is_flag=True, help="Only entries missing embeddings")
@click.pass_context
def embed(ctx, entry_id, embed_all, missing):
    """Generate/update embeddings."""
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


@cli.command()
@click.argument("entry_ids", nargs=-1, required=True)
@click.option("--retrieval", "-r", default=None, help="Retrieval ID to resolve position numbers against")
@click.option("--context", "ctx_text", default=None, help="What query/task made this useful")
@click.option("--kind", default="boost", help="Access kind: boost, cited, acted_on")
@click.option("--source", default="cli", help="Who is boosting")
@click.pass_context
def boost(ctx, entry_ids, retrieval, ctx_text, kind, source):
    """Boost entries that were useful. Improves their ranking in future searches.

    Usage: brain boost --retrieval <rid> 3 7      (by position in that retrieval)
           brain boost <uuid> [<uuid> ...]         (by entry ID directly)

    Position numbers (1, 2, 3...) require --retrieval to avoid race conditions.
    """
    conn = get_conn(ctx.obj["db"])
    cur = conn.cursor()

    # Load retrieval for positional references
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
        # Resolve positional reference against specific retrieval
        if eid.isdigit() and eid in positions:
            eid = positions[eid]
        elif eid.isdigit() and not positions:
            click.echo(f"Position '{eid}' requires --retrieval <id>. Use entry UUIDs directly, or pass -r.", err=True)
            continue

        # Verify entry exists
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
@click.option("--limit", default=20, help="Max results")
@click.pass_context
def boost_history(ctx, entry_id, limit):
    """Show boost/access history for entries."""
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
        # Show most-boosted entries
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
            click.echo(f"  {r['accessed_at']:%Y-%m-%d %H:%M} | {r['kind']} | {r.get('context', '-')}")
    else:
        for r in rows:
            click.echo(f"  [{r['kind']}] {r['title']} — {r['boost_count']} boosts (last: {r['last_boosted']:%Y-%m-%d %H:%M})")


if __name__ == "__main__":
    cli()
