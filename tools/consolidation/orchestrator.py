#!/usr/bin/env python3
"""Consolidation orchestrator — Yoko proposes, Ringo critiques, DB stores.

Pipeline:
  1. Discover clusters of semantically-similar entries (cosine sim >= --threshold).
  2. Union pair-wise links into connected components.
  3. For each cluster, in size order:
     a. If overlaps a pending/deferred proposal, mark old proposal stale on success.
     b. Run Yoko (Opus, with read-only tools) → JSON proposal.
     c. Run Ringo (Sonnet, no tools) → JSON review.
     d. INSERT a row into consolidation_proposals + log.
  4. Render consolidation-YYYY-MM-DD-<db>.md from the DB.

Per-cluster commit. A failed cluster does not roll back earlier ones.

Connects as brain_dream (owner of consolidation_proposals + recall_log).
Yoko's tool calls invoke the brain CLI which connects as brain_cli — those
two sets of credentials are independent.

Usage:
  python3 tools/consolidation/orchestrator.py --db zeresh_brain
      [--threshold 0.93] [--limit N] [--discover-only] [--render-only]
      [--output PATH]

Environment:
  BRAIN_DREAM_DB_USER, BRAIN_DREAM_DB_PASSWORD  — orchestrator's DB creds
  BRAIN_DB_HOST, BRAIN_DB_PORT                  — DB host/port
  Yoko's brain CLI calls additionally need BRAIN_CLI_DB_USER/PASSWORD set.
"""

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras


SCRIPT_DIR = Path(__file__).resolve().parent
YOKO_PROMPT_FILE = SCRIPT_DIR / "yoko-system.md"
RINGO_PROMPT_FILE = SCRIPT_DIR / "ringo-system.md"

DEFAULT_THRESHOLD = 0.93

# Yoko: Claude (Opus) via `claude -p`, agentic, with read-only tools.
YOKO_MODEL = "opus"
# Edit/Write/NotebookEdit are absent from the list, so unavailable. Bash is
# permissive — we rely on the prompt's hard rules to keep her read-only.
# Yoko shells out to the brain CLI, which itself needs brain_cli creds.
YOKO_TOOLS = ["Bash", "Read", "Grep", "Glob"]

# Ringo: GPT-5.4 via Codex direct Responses API. Single completion, no tools.
# (Codex direct util doc explicitly names this consolidation use case as its
# primary consumer. gpt-5.5 is also entitled but markedly more expensive.)
RINGO_MODEL = "gpt-5.4"
CODEX_API_SCRIPT = Path(
    "/home/oc/.openclaw/workspace/jays_code/scripts/codex-direct-api.py"
)

CLAUDE_TIMEOUT_SEC = 600
CODEX_TIMEOUT_SEC = 300

# Run claude -p with cwd set to an empty directory so:
#   - auto-memory finds no matching project slug (empty memory)
#   - CLAUDE.md walk-up from this dir finds no project CLAUDE.md
# Yoko/Ringo still see ~/.claude/CLAUDE.md and ~/.claude/settings.json
# (user-global), which is the trade for keeping subscription auth.
YOKO_CWD = "/tmp/yoko-cwd"


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


# ── cluster discovery ──────────────────────────────────────────────


def discover_clusters(conn, threshold: float):
    """All-pairs cosine similarity, then union-find on links above threshold.

    Returns (clusters, pair_count). clusters is a list of UUID lists.
    """
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    # pgvector <=> is cosine distance (0..2). Similarity = 1 - distance.
    # We compare distance < (1 - threshold) to avoid float-funny on the boundary.
    cur.execute(
        """
        SELECT a.id::text AS a, b.id::text AS b,
               1 - (a.embedding <=> b.embedding) AS sim
        FROM entries a, entries b
        WHERE a.id < b.id
          AND a.embedding IS NOT NULL
          AND b.embedding IS NOT NULL
          AND a.expires_at IS NULL
          AND b.expires_at IS NULL
          AND (a.embedding <=> b.embedding) < %s
        """,
        [1.0 - threshold],
    )
    pairs = cur.fetchall()

    parent: dict[str, str] = {}

    def find(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for r in pairs:
        union(r["a"], r["b"])

    clusters: dict[str, list[str]] = {}
    for x in list(parent.keys()):
        clusters.setdefault(find(x), []).append(x)

    return list(clusters.values()), len(pairs)


def fetch_entries(conn, ids: list[str]):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT id::text, kind, title, body, project, tags, entity_refs,
               source, status, confidence,
               created_at, expires_at, superseded_by::text
        FROM entries
        WHERE id = ANY(%s::uuid[])
        ORDER BY created_at ASC
        """,
        [ids],
    )
    return cur.fetchall()


def fetch_overlapping_pending(conn, ids: list[str]):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT id::text, cluster_entry_ids::text[] AS cluster
        FROM consolidation_proposals
        WHERE status IN ('pending', 'deferred')
          AND cluster_entry_ids && %s::uuid[]
        """,
        [ids],
    )
    return cur.fetchall()


def stale_old_proposal(conn, old_id: str, new_id: str):
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE consolidation_proposals
           SET status = 'stale', superseded_by = %s
         WHERE id = %s
           AND status IN ('pending', 'deferred')
        """,
        [new_id, old_id],
    )
    cur.execute(
        """
        INSERT INTO consolidation_log
            (proposal_id, from_status, to_status, actor, note)
        VALUES (%s, 'pending', 'stale', 'orchestrator',
                'displaced by overlapping new proposal')
        """,
        [old_id],
    )


# ── Yoko (Claude via `claude -p`) / Ringo (GPT via codex_direct_api) ──


def run_yoko(system_prompt: str, user_prompt: str,
             timeout: int = CLAUDE_TIMEOUT_SEC) -> str:
    """Shell out to `claude -p`. Returns the final assistant text (already
    unwrapped from --output-format json's outer object).
    """
    cmd = [
        "claude", "-p", user_prompt,
        "--model", YOKO_MODEL,
        "--system-prompt", system_prompt,
        "--output-format", "json",
        "--no-session-persistence",
        # Note: not using --bare. It would skip auto-memory and CLAUDE.md
        # auto-discovery (good for focus) but also disables OAuth/keychain
        # auth, forcing ANTHROPIC_API_KEY (defeats subscription billing).
        "--tools", " ".join(YOKO_TOOLS),
        "--allowed-tools", " ".join(YOKO_TOOLS),
    ]

    os.makedirs(YOKO_CWD, exist_ok=True)
    res = subprocess.run(
        cmd, capture_output=True, text=True, timeout=timeout, cwd=YOKO_CWD,
    )
    if res.returncode != 0:
        err = res.stderr.strip() or res.stdout.strip()
        raise RuntimeError(f"claude exited {res.returncode}: {err[:500]}")
    try:
        wrapper = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"claude wrapper not JSON: {e}\nstdout (first 500): {res.stdout[:500]}"
        )
    return (wrapper.get("result") or "").strip()


_codex_module = None


def _load_codex():
    """Lazy-load codex-direct-api.py (hyphenated filename → importlib)."""
    global _codex_module
    if _codex_module is None:
        spec = importlib.util.spec_from_file_location(
            "codex_direct_api", CODEX_API_SCRIPT
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"can't locate codex util at {CODEX_API_SCRIPT}")
        _codex_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_codex_module)
    return _codex_module


def run_ringo(system_prompt: str, user_prompt: str,
              timeout: int = CODEX_TIMEOUT_SEC) -> str:
    """Call codex_complete (GPT via the ChatGPT-sub Responses API). Returns
    the model's text output (no wrapper to unpack).
    """
    codex = _load_codex()
    return codex.codex_complete(
        user_prompt,
        instructions=system_prompt,
        model=RINGO_MODEL,
        timeout=timeout,
    ).strip()


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def parse_agent_json(text: str) -> dict:
    """Parse the agent's emitted JSON object. Handles bare JSON, ```json
    fences, and falls back to the first balanced { ... } block.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty agent output")

    # 1) try parsing the whole text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2) strip ```json ... ``` fence
    m = _FENCE_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass

    # 3) take first balanced { ... } block
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i, ch in enumerate(text[start:], start=start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

    raise ValueError(f"couldn't parse JSON from agent output:\n{text[:600]}")


def _calendar_context() -> str:
    """Run `date` and `cal` once. Yoko gets these pre-fed so she doesn't
    burn tool turns asking 'what day is today'. Pure shellouts; if either
    fails for some reason we just return what we have.
    """
    bits = []
    try:
        d = subprocess.run(["date"], capture_output=True, text=True, timeout=5)
        if d.returncode == 0:
            bits.append(f"date: {d.stdout.strip()}")
    except Exception:
        pass
    try:
        c = subprocess.run(["cal"], capture_output=True, text=True, timeout=5)
        if c.returncode == 0:
            bits.append("cal (this month):\n" + c.stdout.rstrip())
    except Exception:
        pass
    return "\n\n".join(bits)


def yoko_user_prompt(entries) -> str:
    cluster_json = json.dumps(
        [dict(e) for e in entries], default=str, indent=2
    )
    cal_ctx = _calendar_context()
    return (
        f"{cal_ctx}\n\n"
        "Review this cluster of semantically-similar brain entries and "
        "propose ONE action per the rules in your system prompt. "
        "Investigate actively before deciding — search beyond the cluster. "
        "Output strict JSON only — a single object, no prose around it.\n\n"
        f"CLUSTER ({len(entries)} entries):\n{cluster_json}"
    )


def ringo_user_prompt(entries, yoko_output: dict) -> str:
    cluster_json = json.dumps(
        [dict(e) for e in entries], default=str, indent=2
    )
    yoko_json = json.dumps(yoko_output, indent=2)
    return (
        "Critique Yoko's consolidation proposal. Output strict JSON only.\n\n"
        f"CLUSTER ({len(entries)} entries):\n{cluster_json}\n\n"
        f"YOKO'S PROPOSAL:\n{yoko_json}"
    )


# ── DB writes ──────────────────────────────────────────────────────


def insert_proposal(conn, cluster_ids: list[str],
                    yoko: dict, ringo: dict) -> str:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO consolidation_proposals
            (cluster_entry_ids, yoko_proposal, ringo_review, action,
             issue_confidence, resolution_confidence,
             agreement, thoroughness,
             yoko_model, ringo_model)
        VALUES (%s::uuid[], %s::jsonb, %s::jsonb, %s,
                %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        [
            cluster_ids,
            json.dumps(yoko),
            json.dumps(ringo),
            yoko.get("action", "unknown"),
            yoko.get("issue_confidence"),
            yoko.get("resolution_confidence"),
            ringo.get("agreement"),
            ringo.get("thoroughness"),
            YOKO_MODEL,
            RINGO_MODEL,
        ],
    )
    new_id = cur.fetchone()[0]
    cur.execute(
        """
        INSERT INTO consolidation_log
            (proposal_id, from_status, to_status, actor, note)
        VALUES (%s, NULL, 'pending', 'orchestrator', 'created')
        """,
        [new_id],
    )
    return str(new_id)


# ── markdown render ───────────────────────────────────────────────


def render_markdown(conn, output_path: Path, db_name: str):
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT id::text,
               cluster_entry_ids::text[] AS cluster,
               yoko_proposal, ringo_review,
               action, issue_confidence, resolution_confidence,
               agreement, thoroughness,
               status, created_at
        FROM consolidation_proposals
        WHERE status = 'pending'
        ORDER BY created_at DESC
        """
    )
    rows = cur.fetchall()

    lines: list[str] = []
    lines.append(f"# Consolidation review — {db_name} — {datetime.now().date()}")
    lines.append("")
    lines.append(f"{len(rows)} pending proposal(s).")
    lines.append("")
    lines.append("## How to read this file")
    lines.append("")
    lines.append("Each proposal shows the full source entries, Yoko's proposed action, "
                 "and Ringo's critique. Decide each with one of:")
    lines.append("")
    lines.append("- `brain approve <id>` — apply the proposal")
    lines.append("- `brain deny <id>` — reject (won't be re-proposed for the same cluster)")
    lines.append("- `brain defer <id>` — skip (may re-propose if the cluster changes)")
    lines.append("- `brain escalate <id>` — flag for deeper investigation")
    lines.append("")
    lines.append("**Confidence scales** (all on 0..1):")
    lines.append("")
    lines.append("- Yoko's `issue_confidence` — how sure Yoko is that *something* in the "
                 "cluster needs change.")
    lines.append("- Yoko's `resolution_confidence` — how sure Yoko is that *her specific "
                 "proposed action* is the right fix.")
    lines.append("- Ringo's `agreement` — how much Ringo agrees with Yoko's chosen action.")
    lines.append("- Ringo's `thoroughness` — how thoroughly Ringo thinks Yoko investigated.")
    lines.append("")
    lines.append("**Ringo's objection severity:**")
    lines.append("")
    lines.append("- `low` — minor concern; proposal likely still fine if approved.")
    lines.append("- `medium` — concern that merits a second look before approving.")
    lines.append("- `high` — proposal should likely be denied or escalated.")
    lines.append("")

    for r in rows:
        lines.extend(_render_proposal(cur, r))

    output_path.write_text("\n".join(lines))


def _fmt_score(v) -> str:
    if v is None:
        return "_(n/a)_"
    try:
        return f"**{float(v):.2f}** / 1.0"
    except (TypeError, ValueError):
        return str(v)


def _fmt_list(items, fallback: str = "_(none)_") -> str:
    if not items:
        return fallback
    return ", ".join(str(x) for x in items)


def _blockquote(text: str) -> list[str]:
    if not text:
        return ["> _(none)_"]
    return [f"> {line}" if line else ">" for line in str(text).splitlines()]


def _render_action_args(action: str, args: dict) -> list[str]:
    """Render the proposed change in human-readable form, no JSON."""
    out: list[str] = []
    args = args or {}

    if action == "merge-supersede":
        new = args.get("new_entry", {})
        out.append("**The new entry would be:**")
        out.append("")
        bits = []
        if new.get("kind"):
            bits.append(f"**{new['kind']}**")
        if new.get("status"):
            bits.append(f"status: `{new['status']}`")
        if new.get("confidence") is not None:
            bits.append(f"confidence: `{new['confidence']}`")
        if bits:
            out.append("- " + " · ".join(bits))
        if new.get("title"):
            out.append(f"- title: **{new['title']}**")
        if new.get("project"):
            out.append(f"- project: `{new['project']}`")
        if "tags" in new:
            out.append(f"- tags: {_fmt_list(new.get('tags'))}")
        if "entity_refs" in new:
            out.append(f"- entity_refs: {_fmt_list(new.get('entity_refs'))}")
        out.append("")
        out.append("body:")
        out.append("")
        out.extend(_blockquote(new.get("body", "")))
        out.append("")
        out.append("**Superseding** these entries:")
        for sid in args.get("supersede_ids", []) or []:
            out.append(f"- `{sid[:8]}`")

    elif action == "update-status":
        out.append(
            f"**Update entry** `{(args.get('entry_id') or '?')[:8]}` "
            f"**status →** `{args.get('new_status', '?')}`"
        )
        ev = args.get("evidence_refs") or []
        if ev:
            out.append("")
            out.append("Evidence cited:")
            for e in ev:
                out.append(f"- {e}")

    elif action == "fix-metadata":
        out.append(
            f"**Update entry** `{(args.get('entry_id') or '?')[:8]}` "
            f"**`{args.get('field', '?')}` →** `{args.get('new_value', '?')}`"
        )

    elif action == "flag-contradiction":
        out.append("**Contradicting entries:**")
        for eid in args.get("entry_ids", []) or []:
            out.append(f"- `{eid[:8]}`")
        if args.get("claims"):
            out.append("")
            out.append("**Claims:**")
            for c in args["claims"]:
                out.append(f"- {c}")
        if args.get("checks_attempted"):
            out.append("")
            out.append("**Checks attempted:**")
            for c in args["checks_attempted"]:
                out.append(f"- {c}")
        if args.get("what_would_resolve_it"):
            out.append("")
            out.append(f"**What would resolve it:** {args['what_would_resolve_it']}")

    elif action == "no-action":
        out.append(f"**No action.** Reason: _{args.get('reason', '?')}_")

    elif action == "defer-to-human":
        if args.get("what_we_know"):
            out.append("**What we know:**")
            out.extend(_blockquote(args["what_we_know"]))
            out.append("")
        if args.get("what_we_dont_know"):
            out.append("**What we don't know:**")
            out.extend(_blockquote(args["what_we_dont_know"]))
            out.append("")
        if args.get("suggested_human_action"):
            out.append(f"**Suggested human action:** {args['suggested_human_action']}")
        if args.get("suggested_v2_action"):
            out.append(f"**Suggested v2 action:** {args['suggested_v2_action']}")

    else:
        out.append(f"_Unrecognised action `{action}` — args:_")
        out.append("```")
        out.append(json.dumps(args, indent=2))
        out.append("```")

    return out


def _render_evidence(evidence: list) -> list[str]:
    if not evidence:
        return ["_(none recorded)_"]
    out = []
    for item in evidence:
        if not isinstance(item, dict):
            out.append(f"- {item}")
            continue
        kind = item.get("kind")
        if kind == "tool_call":
            tool = item.get("tool", "?")
            inp = item.get("input", "")
            summ = item.get("result_summary", "")
            out.append(f"- _tool_ `{tool}` — input: `{inp}` → {summ}")
        elif kind == "entry":
            eid = (item.get("id") or "?")[:8]
            why = item.get("why_relevant", "")
            out.append(f"- _entry_ `{eid}` — {why}")
        else:
            out.append(f"- {json.dumps(item)}")
    return out


def _render_proposal(cur, r: dict) -> list[str]:
    yoko = r["yoko_proposal"] or {}
    ringo = r["ringo_review"] or {}
    cluster = r["cluster"]
    pid8 = r["id"][:8]

    L: list[str] = []
    L.append("---")
    L.append("")
    L.append(f"## Proposal `{pid8}` — {r['action']}")
    L.append("")
    L.append(f"_Created {r['created_at']:%Y-%m-%d %H:%M}._")
    L.append("")
    L.append("| Score | Value |")
    L.append("|---|---|")
    L.append(f"| Yoko `issue_confidence` (something needs change) | {_fmt_score(r['issue_confidence'])} |")
    L.append(f"| Yoko `resolution_confidence` (this is the right fix) | {_fmt_score(r['resolution_confidence'])} |")
    L.append(f"| Ringo `agreement` with Yoko's action | {_fmt_score(r['agreement'])} |")
    L.append(f"| Ringo `thoroughness` of Yoko's investigation | {_fmt_score(r['thoroughness'])} |")
    L.append("")

    # ── source cluster — full payloads ────────────────────────────
    L.append(f"### The cluster — {len(cluster)} entries")
    L.append("")
    cur.execute(
        """
        SELECT id::text, kind, title, body, project, tags, entity_refs,
               source, status, confidence, created_at
        FROM entries WHERE id = ANY(%s::uuid[]) ORDER BY created_at ASC
        """,
        [cluster],
    )
    entries = cur.fetchall()
    for i, e in enumerate(entries, 1):
        L.append(f"#### Entry {i} of {len(entries)} — `{e['id'][:8]}`")
        L.append("")
        meta1 = [f"**{e['kind']}**"]
        if e.get("status"):
            meta1.append(f"status: `{e['status']}`")
        if e.get("confidence") is not None:
            meta1.append(f"confidence: `{e['confidence']}`")
        L.append("- " + " · ".join(meta1))
        L.append(f"- by `{e['source'] or '?'}` · project: "
                 f"`{e['project']}`" if e.get('project') else
                 f"- by `{e['source'] or '?'}` · _(no project)_")
        L.append(f"- tags: {_fmt_list(e.get('tags'))}")
        L.append(f"- entity_refs: {_fmt_list(e.get('entity_refs'))}")
        L.append(f"- created: {e['created_at']:%Y-%m-%d %H:%M}")
        L.append(f"- title: **{e['title']}**")
        L.append("")
        L.append("body:")
        L.append("")
        L.extend(_blockquote(e.get("body", "")))
        L.append("")

    # ── Yoko's proposal ────────────────────────────────────────────
    L.append("### Yoko proposes")
    L.append("")
    L.append("Reasoning:")
    L.append("")
    L.extend(_blockquote(yoko.get("reasoning", "")))
    L.append("")
    if yoko.get("uncertainties"):
        L.append("Uncertainties:")
        L.append("")
        L.extend(_blockquote(yoko["uncertainties"]))
        L.append("")
    L.extend(_render_action_args(r["action"], yoko.get("action_args") or {}))
    L.append("")
    L.append("Evidence Yoko gathered:")
    L.append("")
    L.extend(_render_evidence(yoko.get("evidence") or []))
    L.append("")

    # ── Ringo's review ─────────────────────────────────────────────
    L.append("### Ringo reviews")
    L.append("")
    objs = ringo.get("objections") or []
    if not objs:
        L.append("_No objections._")
    else:
        # Sort high → medium → low so blockers stand out first.
        order = {"high": 0, "medium": 1, "low": 2}
        objs_sorted = sorted(objs, key=lambda o: order.get(
            (o.get("severity") or "").lower(), 9))
        for j, obj in enumerate(objs_sorted, 1):
            sev = (obj.get("severity") or "?").lower()
            issue = obj.get("issue", "?")
            alt = obj.get("alternative", "")
            L.append(f"**Objection {j} — severity: `{sev}`**")
            L.append("")
            L.extend(_blockquote(issue))
            if alt:
                L.append("")
                L.append("_Alternative:_")
                L.append("")
                L.extend(_blockquote(alt))
            L.append("")
    if ringo.get("what_yoko_might_have_missed"):
        L.append("Other things Ringo thinks Yoko might have missed:")
        L.append("")
        L.extend(_blockquote(ringo["what_yoko_might_have_missed"]))
        L.append("")

    # ── decide ────────────────────────────────────────────────────
    L.append("### Decide")
    L.append("")
    L.append(f"- `brain approve {pid8}` — apply the proposal")
    L.append(f"- `brain deny {pid8}` — reject (won't be re-proposed for the same cluster)")
    L.append(f"- `brain defer {pid8}` — skip (may re-propose if the cluster changes)")
    L.append(f"- `brain escalate {pid8}` — flag for deeper investigation")
    L.append("")

    return L


# ── main ──────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--db", default=os.environ.get("BRAIN_DB_NAME", "zeresh_brain"))
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help=f"Cosine similarity threshold (default {DEFAULT_THRESHOLD}).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Process at most N clusters (sanity-check runs).")
    ap.add_argument("--discover-only", action="store_true",
                    help="Print discovered clusters and exit.")
    ap.add_argument("--render-only", action="store_true",
                    help="Skip discovery; just (re)render markdown from DB.")
    ap.add_argument("--output", default=None,
                    help="Markdown output path. Default: "
                         "consolidation-YYYY-MM-DD-<db>.md")
    args = ap.parse_args()

    yoko_prompt = YOKO_PROMPT_FILE.read_text()
    ringo_prompt = RINGO_PROMPT_FILE.read_text()

    conn = db_connect(args.db)
    output_path = Path(
        args.output or f"consolidation-{datetime.now().date()}-{args.db}.md"
    )

    if args.render_only:
        render_markdown(conn, output_path, args.db)
        print(f"Rendered → {output_path}")
        return

    print(f"Discovering clusters at sim ≥ {args.threshold} in {args.db}...")
    clusters, pair_count = discover_clusters(conn, args.threshold)
    print(f"  {pair_count} pairs above threshold")
    print(f"  {len(clusters)} connected components")
    if clusters:
        sizes = sorted([len(c) for c in clusters], reverse=True)
        print(f"  cluster sizes: {sizes}")

    # Smaller clusters first — easier to reason about; deterministic ordering.
    clusters.sort(key=lambda c: (len(c), sorted(c)[0]))

    if args.discover_only:
        for i, cluster in enumerate(clusters):
            entries = fetch_entries(conn, cluster)
            print(f"\nCluster {i + 1} ({len(cluster)} entries):")
            for e in entries:
                print(f"  {e['id'][:8]} [{e['kind']}] {e['title']}")
        return

    if args.limit is not None:
        clusters = clusters[: args.limit]
        print(f"  Processing first {len(clusters)} clusters")

    fail_count = 0
    success_count = 0
    for i, cluster in enumerate(clusters, start=1):
        print(f"\n=== Cluster {i}/{len(clusters)} ({len(cluster)} entries) ===")
        entries = fetch_entries(conn, cluster)
        for e in entries:
            print(f"  - [{e['kind']}] {e['title']}")

        overlap = fetch_overlapping_pending(conn, cluster)
        for old in overlap:
            print(f"  ! overlaps pending proposal {old['id'][:8]} — will mark stale")

        try:
            t0 = time.time()
            print(f"  → Yoko (Claude {YOKO_MODEL})...")
            yoko_text = run_yoko(yoko_prompt, yoko_user_prompt(entries))
            yoko = parse_agent_json(yoko_text)
            print(f"    {time.time() - t0:.0f}s · action={yoko.get('action')} "
                  f"issue={yoko.get('issue_confidence')} "
                  f"resolution={yoko.get('resolution_confidence')}")

            t0 = time.time()
            print(f"  → Ringo (Codex {RINGO_MODEL})...")
            ringo_text = run_ringo(ringo_prompt, ringo_user_prompt(entries, yoko))
            ringo = parse_agent_json(ringo_text)
            print(f"    {time.time() - t0:.0f}s · "
                  f"agreement={ringo.get('agreement')} "
                  f"thoroughness={ringo.get('thoroughness')}")

            new_id = insert_proposal(conn, cluster, yoko, ringo)
            for old in overlap:
                stale_old_proposal(conn, old["id"], new_id)
            conn.commit()
            print(f"  ✓ proposal {new_id[:8]}")
            success_count += 1
        except Exception as e:
            conn.rollback()
            fail_count += 1
            print(f"  ✗ failed: {e}", file=sys.stderr)

    print(f"\nDone. {success_count} succeeded, {fail_count} failed.")

    render_markdown(conn, output_path, args.db)
    print(f"Rendered → {output_path}")
    conn.close()
    if fail_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
