#!/usr/bin/env python3
"""Backfill recall_log from the retrievals table.

The retrievals table records every search query and the ordered list of
entry UUIDs returned. That's a retrospective view of recall — the same
signal dreaming wants to score against, just stored in a coarser shape
(one row per query, not one per (query, entry) pair).

This script replays every retrievals row into recall_log as one row per
result-position, honoring the original timestamp so time-decayed signals
(recency, frequency-over-window) reflect real history rather than
backfill time.

Runs as brain_dream (owner of recall_log). Idempotent: refuses to re-run
if any source='backfill-retrievals' rows already exist, unless --force.

Fields populated:
  entry_id     — result_ids[position - 1]
  query        — retrievals.query
  rank         — 1-indexed position
  score        — NULL (not captured historically)
  session_key  — NULL (not captured historically)
  source       — 'backfill-retrievals'  (marker)
  context      — 'backfill from retrieval <uuid>'  (audit trail)
  recalled_at  — retrievals.created_at  (original timestamp)

Usage:
  python3 tools/backfill-recall-log.py --db zeresh_brain [--dry-run] [--force]

Environment: expects BRAIN_DB_HOST, BRAIN_DB_PORT, BRAIN_DREAM_DB_USER,
BRAIN_DREAM_DB_PASSWORD to be set (loaded from .env by the wrapper).
"""

import argparse
import os
import sys
import time

import psycopg2
import psycopg2.extras


BACKFILL_MARKER = "backfill-retrievals"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("BRAIN_DB_NAME", "zeresh_brain"),
                    help="Database to backfill (default: $BRAIN_DB_NAME or zeresh_brain). "
                         "Each brain has its own retrieval history — run once per brain.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be inserted, without writing.")
    ap.add_argument("--force", action="store_true",
                    help="Proceed even if backfill rows already exist. Use with care.")
    args = ap.parse_args()

    user = os.environ.get("BRAIN_DREAM_DB_USER")
    pw = os.environ.get("BRAIN_DREAM_DB_PASSWORD")
    if not user or not pw:
        sys.exit("BRAIN_DREAM_DB_USER / BRAIN_DREAM_DB_PASSWORD must be set.")

    conn = psycopg2.connect(
        dbname=args.db,
        host=os.environ.get("BRAIN_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("BRAIN_DB_PORT", "5432")),
        user=user,
        password=pw,
    )
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Idempotency gate
    cur.execute("SELECT count(*) AS n FROM recall_log WHERE source = %s", [BACKFILL_MARKER])
    existing = cur.fetchone()["n"]
    if existing and not args.force:
        sys.exit(
            f"Refusing to run: {existing} rows with source='{BACKFILL_MARKER}' already exist. "
            f"Pass --force to re-run (rows will duplicate)."
        )
    if existing and args.force:
        print(f"WARNING: {existing} backfill rows already exist; --force will add more.")

    # Count rows we're about to produce (UNNEST the result_ids array)
    cur.execute("SELECT count(*) AS n FROM retrievals")
    n_retrievals = cur.fetchone()["n"]
    cur.execute("""
        SELECT count(*) AS n
        FROM retrievals r, unnest(r.result_ids) WITH ORDINALITY
    """)
    n_recall_rows = cur.fetchone()["n"]
    print(f"retrievals rows to replay: {n_retrievals}")
    print(f"recall_log rows to insert: {n_recall_rows}")

    if n_recall_rows == 0:
        print("Nothing to backfill.")
        return

    if args.dry_run:
        cur.execute("""
            SELECT substring(r.query, 1, 40) AS q,
                   array_length(r.result_ids, 1) AS n,
                   r.created_at
            FROM retrievals r
            ORDER BY r.created_at ASC LIMIT 3
        """)
        for r in cur.fetchall():
            print(f"  sample: q={r['q']!r} -> {r['n']} entries at {r['created_at']:%Y-%m-%d %H:%M}")
        print("Dry run: no writes performed.")
        return

    # One-shot INSERT ... SELECT with UNNEST WITH ORDINALITY.
    # WITH ORDINALITY gives us the 1-indexed rank for free.
    t0 = time.time()
    cur.execute("""
        INSERT INTO recall_log
            (entry_id, query, rank, score, session_key, source, context, recalled_at)
        SELECT u.entry_id,
               r.query,
               u.rank::int,
               NULL,                              -- score not captured historically
               NULL,                              -- session_key not captured
               %s,                                -- source marker
               'backfill from retrieval ' || r.id::text,
               r.created_at                       -- honor historical timestamp
        FROM retrievals r,
             unnest(r.result_ids) WITH ORDINALITY AS u(entry_id, rank)
        ORDER BY r.created_at, u.rank
    """, [BACKFILL_MARKER])
    inserted = cur.rowcount
    conn.commit()
    elapsed = time.time() - t0
    print(f"Inserted {inserted} rows in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
