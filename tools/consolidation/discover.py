#!/usr/bin/env python3
"""Consolidation cluster discovery — deterministic, zero-token.

Finds clusters of semantically-similar active brain entries (pgvector
cosine similarity above a threshold, pair links unioned into connected
components) and reconciles them against the engine task queue, so each
cluster is asked about exactly once. This is the discovery half of the
split dreamer-janitor design (engine task 2a09b9a3): a cheap gate/filer
that spends no model tokens; the per-cluster authoring runs are
dispatched separately by the engine.

Cluster exclusion rules (the dedup memory lives in the engine queue):
  - An entry that is soft-deleted (expires_at) or already superseded is
    never clustered. (The superseded filter fixes a v1 bug: after a
    merge lands, the originals sit at cosine ~0.99 of the merged entry
    and would be rediscovered forever.)
  - A cluster whose exact entry set matches the 'brain:<id>' sources of
    ANY existing engine task (any status) is skipped — it was already
    asked, and a deny/abandon must stay denied. If the cluster's
    membership changed, it is a different question and is filed anew.
  - A cluster that merely OVERLAPS an OPEN task's entries is skipped for
    this run — no parallel work on shared entries; it re-emerges once
    the open task resolves.

Modes:
  --dry-run      (default) print the clusters that would be filed
  --gate         exit 0 with a summary on stdout if at least one cluster
                 needs filing, exit 1 otherwise — the janitor-gate
                 contract (stdout becomes the charter's context)
  --file-tasks   file one engine task per cluster (via the engine CLI,
                 the only sanctioned write path) and print what was filed

Environment: BRAIN_DB_HOST/PORT, BRAIN_DREAM_DB_USER/PASSWORD (read-only
use; dreaming infrastructure runs as brain_dream), ENGINE_BIN (optional,
default 'engine' on PATH).

Usage:
  python3 tools/consolidation/discover.py [--db zeresh_brain]
      [--threshold 0.93] [--dry-run | --gate | --file-tasks]
      [--max-tasks N]
"""

import argparse
import json
import os
import re
import subprocess
import sys

import psycopg2
import psycopg2.extras


ENGINE_BIN = os.environ.get("ENGINE_BIN", "engine")
FILER_IDENTITY = "janitor:brain-discovery"
ENGINE_PROJECT = "brain"
TASK_TITLE_PREFIX = "Consolidate brain cluster:"

# Time-series screens. Legitimate periodic snapshots (daily healthchecks,
# morning briefings, session debriefs) cluster tightly forever: every new
# day's entry changes the cluster membership, defeating exact-set dedup,
# and an author run would keep answering "no-action, time-series". Two
# deterministic screens catch them before any tokens are spent; the
# charter's time-series hard rule remains the backstop for what slips
# through.
DEFAULT_EXCLUDE_TAGS = {"morning-briefing", "healthcheck", "daily-report"}

# Dates in titles, for the dated-title heuristic: ISO (2026-03-20) and
# month-name (Mar 24 / March 24, 2026) forms.
_TITLE_DATE_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
    r"\s+\d{1,2}(?:,?\s+\d{4})?",
    re.IGNORECASE)


def is_time_series(entries) -> str | None:
    """Return a reason string if the cluster is a periodic snapshot
    series, else None. Deterministic, conservative.

    A cluster only counts as a series if its titles carry at least two
    DISTINCT dates — the same date recorded twice is a duplicate (exactly
    what consolidation is for), not a series."""
    tag_sets = [set(e["tags"] or []) for e in entries]
    common_excluded = set.intersection(*tag_sets) & DEFAULT_EXCLUDE_TAGS \
        if tag_sets else set()
    if common_excluded:
        return f"time-series (shared tag: {sorted(common_excluded)[0]})"

    matches = [_TITLE_DATE_RE.search(e["title"]) for e in entries]
    if not all(matches):
        return None
    dates = {m.group(0).lower() for m in matches}
    if len(dates) < 2:
        return None
    stripped = {" ".join(_TITLE_DATE_RE.sub("", e["title"]).split()).lower()
                for e in entries}
    if len(stripped) == 1:
        return "time-series (identical titles after date removal)"
    prefixes = {e["title"][:m.start()].strip().lower()
                for e, m in zip(entries, matches)}
    if len(prefixes) == 1 and len(next(iter(prefixes))) >= 6:
        return (f"time-series (shared title prefix "
                f"'{next(iter(prefixes))[:40]}' with differing dates)")
    return None


def db_connect(db_name: str):
    user = os.environ.get("BRAIN_DREAM_DB_USER")
    pw = os.environ.get("BRAIN_DREAM_DB_PASSWORD")
    if not user or not pw:
        sys.exit("BRAIN_DREAM_DB_USER / BRAIN_DREAM_DB_PASSWORD must be set.")
    return psycopg2.connect(
        dbname=db_name,
        host=os.environ.get("BRAIN_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("BRAIN_DB_PORT", "5432")),
        user=user,
        password=pw,
    )


def discover_clusters(conn, threshold: float):
    """All-pairs cosine similarity over live entries, union-find into
    connected components. Live = not soft-deleted AND not superseded."""
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT a.id::text AS a, b.id::text AS b
        FROM entries a, entries b
        WHERE a.id < b.id
          AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
          AND a.expires_at IS NULL AND b.expires_at IS NULL
          AND a.superseded_by IS NULL AND b.superseded_by IS NULL
          AND (a.embedding <=> b.embedding) < %s
        """,
        [1.0 - threshold],
    )
    pairs = cur.fetchall()

    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in pairs:
        ra, rb = find(r["a"]), find(r["b"])
        if ra != rb:
            parent[ra] = rb

    clusters: dict = {}
    for x in list(parent.keys()):
        clusters.setdefault(find(x), []).append(x)
    return [sorted(c) for c in clusters.values()], len(pairs)


def fetch_entries(conn, ids):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT id::text, kind, title, body, project, tags, entity_refs,
               source, status, confidence, created_at
        FROM entries WHERE id = ANY(%s::uuid[])
        ORDER BY created_at ASC
        """,
        [ids],
    )
    return cur.fetchall()


def engine_json(args: list):
    """Run the engine CLI and parse its JSON output."""
    res = subprocess.run([ENGINE_BIN] + args, capture_output=True, text=True,
                         timeout=60)
    if res.returncode != 0:
        raise RuntimeError(f"engine {' '.join(args[:3])}... failed: "
                           f"{(res.stderr or res.stdout).strip()[:300]}")
    return json.loads(res.stdout)


def existing_brain_task_sources():
    """Return (all_source_sets, open_source_sets): for every brain-project
    engine task, the frozenset of brain entry ids in its sources.
    Read via the CLI only — the queue database is never touched directly."""
    all_sets, open_sets = [], []
    tasks = engine_json(["task", "list", "--project", ENGINE_PROJECT, "--all"])
    for t in tasks:
        detail = engine_json(["task", "show", t["id"]])
        ids = frozenset(s.split(":", 1)[1] for s in (detail.get("sources") or [])
                        if isinstance(s, str) and s.startswith("brain:"))
        if not ids:
            continue
        all_sets.append(ids)
        if t["status"] not in ("done", "abandoned"):
            open_sets.append(ids)
    return all_sets, open_sets


def reconcile(conn, clusters, all_sets, open_sets):
    """Split clusters into (to_file, skipped) per the exclusion rules."""
    to_file, skipped = [], []
    for c in clusters:
        cset = frozenset(c)
        if any(cset == s for s in all_sets):
            skipped.append({"cluster": c, "reason": "already asked "
                            "(exact entry set matches an existing task)"})
            continue
        if any(cset & s for s in open_sets):
            skipped.append({"cluster": c, "reason": "overlaps an open task"})
            continue
        ts_reason = is_time_series(fetch_entries(conn, c))
        if ts_reason:
            skipped.append({"cluster": c, "reason": ts_reason})
            continue
        to_file.append(c)
    return to_file, skipped


def render_task_body(entries) -> str:
    """Plain, complete-sentence body per engine task-nutrition rules:
    a different-lineage author with none of our context, and an offline
    reviewer, must both be able to work from this alone."""
    lines = []
    lines.append(
        f"These {len(entries)} zeresh_brain entries are semantically "
        "near-duplicates (pgvector cosine similarity above the discovery "
        "threshold). Review them and propose exactly one consolidation "
        "action following the brain consolidation charter at "
        "tools/consolidation/author-charter.md in the zeresh-brain "
        "repository (the brain genius's canonical checkout). The charter "
        "defines the available actions, the hard rules, and the staging "
        "JSON format your proposal must be written in.")
    lines.append("")
    for i, e in enumerate(entries, 1):
        lines.append(f"--- Entry {i} of {len(entries)} — id {e['id']} ---")
        lines.append(f"kind: {e['kind']} | status: {e['status']} | "
                     f"confidence: {e['confidence']}")
        lines.append(f"author (source): {e['source'] or 'unknown'} | "
                     f"project: {e['project'] or 'none'}")
        lines.append(f"tags: {', '.join(e['tags'] or []) or 'none'} | "
                     f"entity_refs: {', '.join(e['entity_refs'] or []) or 'none'}")
        lines.append(f"created: {e['created_at']:%Y-%m-%d %H:%M}")
        lines.append(f"title: {e['title']}")
        lines.append("body:")
        lines.append(e["body"] or "(empty)")
        lines.append("")
    return "\n".join(lines)


def file_task(entries) -> str:
    first_title = entries[0]["title"][:60]
    extra = len(entries) - 1
    title = (f"{TASK_TITLE_PREFIX} {first_title}"
             + (f" (+{extra} similar)" if extra else ""))
    args = ["task", "new", "--project", ENGINE_PROJECT,
            "--by", FILER_IDENTITY,
            "--title", title,
            "--body", render_task_body(entries)]
    for e in entries:
        args += ["--source", f"brain:{e['id']}"]
    out = engine_json(args)
    return out["id"]


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("BRAIN_DB_NAME",
                                                   "zeresh_brain"))
    ap.add_argument("--threshold", type=float, default=0.93)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--gate", action="store_true",
                      help="janitor-gate mode: exit 0 iff work exists")
    mode.add_argument("--file-tasks", action="store_true",
                      help="file one engine task per new cluster")
    ap.add_argument("--max-tasks", type=int, default=10,
                    help="cap on tasks filed per run (default 10) — keeps "
                         "one bad threshold from flooding the queue")
    args = ap.parse_args()

    conn = db_connect(args.db)
    clusters, pair_count = discover_clusters(conn, args.threshold)
    all_sets, open_sets = existing_brain_task_sources()
    to_file, skipped = reconcile(conn, clusters, all_sets, open_sets)

    summary = (f"{pair_count} similar pairs, {len(clusters)} clusters; "
               f"{len(to_file)} need filing, {len(skipped)} skipped "
               f"(already asked, overlapping open work, or time-series).")

    if args.gate:
        print(summary)
        for c in to_file[:args.max_tasks]:
            titles = [e["title"][:60] for e in fetch_entries(conn, c)]
            print(f"  cluster of {len(c)}: " + " / ".join(titles))
        sys.exit(0 if to_file else 1)

    if args.file_tasks:
        filed = []
        for c in to_file[:args.max_tasks]:
            entries = fetch_entries(conn, c)
            tid = file_task(entries)
            filed.append({"task": tid, "entries": c})
            print(f"filed {tid[:8]}: cluster of {len(c)} "
                  f"({entries[0]['title'][:50]}...)")
        overflow = len(to_file) - len(filed)
        if overflow > 0:
            print(f"NOTE: {overflow} more clusters exceeded --max-tasks; "
                  "they will be filed on the next run.")
        print(summary)
        return

    # dry run
    print(summary)
    for c in to_file:
        entries = fetch_entries(conn, c)
        print(f"\nWould file: cluster of {len(c)}")
        for e in entries:
            print(f"  {e['id'][:8]} [{e['kind']}] {e['title'][:70]}")
    for s in skipped:
        print(f"\nSkipped ({s['reason']}): "
              + ", ".join(x[:8] for x in s["cluster"]))


if __name__ == "__main__":
    main()
