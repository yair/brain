#!/usr/bin/env python3
"""Dreamer dispatch — deterministic authoring/review driver for brain
consolidation tasks.

The discovery janitor files one engine task per cluster. This script is
the dispatch half: it picks up unclaimed consolidation tasks and runs
the author (Claude Opus, engine author role profile) and then the
cross-lineage reviewer (Codex) on whatever the author proposed. It is
pure machinery — the only model tokens spent are the author's and the
reviewer's own runs. Adjudication stays human; apply stays with the
engine warden.

Runs as the charter of the brain-dispatch janitor (warden-gated), or by
hand. One task per invocation by default: the warden ticks every
15 minutes, which paces a backlog gently.

Modes:
  --gate       exit 0 iff there is dispatchable work (cheap; two engine
               CLI calls), printing a one-line summary for the charter
  (default)    dispatch up to --max tasks end to end

Environment: `engine` and `brain` on PATH (the brain wrapper sources its
own .env; engine reads ~/.config/engine/db.env). The author needs no
extra env — its brain/engine calls resolve the same way.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

ENGINE = os.environ.get("ENGINE_BIN", "engine")
BRAIN_REPO = "/home/oc/projects/zeresh-brain"
CHARTER = os.path.join(BRAIN_REPO, "tools/consolidation/author-charter.md")
STAGING_ROOT = os.path.expanduser("~/.local/state/brain-dreamer/changes")
RUN_LOG_DIR = os.path.expanduser("~/.local/state/brain-dreamer/runs")
TITLE_MARKER = "Consolidate brain cluster:"
AUTHOR = "brain-author:opus"
REVIEWER = "brain-reviewer:codex"

PACK_HEADER = """\
# Your run

You are the brain consolidation author, working one task of the engine
work system. Operational facts for this run:

- Your identity for every engine and brain command: `{author}`
- Your task id: {tid8} (already claimed for you)
- Your staging directory (your current working directory):
  {staging}
- The brain repo (charter, rubric, CLI docs): {repo}
- The `brain` and `engine` CLIs are on PATH and configured. Pass
  `--json` to brain commands. Always pass `--by {author}` to engine
  commands.
- The reviewer receives the brain rubric automatically — do NOT copy
  it into your staging directory.
- If you propose a change, proposing moves the task to in-review on
  its own; just end your run. For non-change outcomes, follow the
  charter (receipt DONE / NEEDS_INPUT as it says).

Your charter follows, then the task record.

---

"""


def engine_json(args, timeout=120):
    res = subprocess.run([ENGINE] + args, capture_output=True, text=True,
                         timeout=timeout)
    if res.returncode != 0:
        raise RuntimeError("engine %s failed: %s"
                           % (" ".join(args[:3]),
                              (res.stderr or res.stdout).strip()[:300]))
    return json.loads(res.stdout)


def dispatchable_tasks():
    """Unclaimed consolidation tasks, oldest first (list is priority/age
    ordered already)."""
    tasks = engine_json(["task", "list", "--project", "brain",
                         "--status", "todo"])
    return [t for t in tasks
            if t["title"].startswith(TITLE_MARKER)
            and not t.get("claimed_by")]


def dispatch_one(task):
    tid = task["id"]
    tid8 = tid[:8]
    outcome = {"task": tid8, "title": task["title"][:60]}

    # Claim (atomic; losing the race is a clean skip).
    try:
        engine_json(["task", "claim", tid, "--by", AUTHOR])
    except RuntimeError as e:
        outcome["result"] = "claim-lost"
        outcome["detail"] = str(e)[:120]
        return outcome

    staging = os.path.join(STAGING_ROOT, tid8)
    os.makedirs(staging, exist_ok=True)
    os.makedirs(RUN_LOG_DIR, exist_ok=True)

    detail = engine_json(["task", "show", tid])
    with open(CHARTER) as fh:
        charter = fh.read()
    pack = (PACK_HEADER.format(author=AUTHOR, tid8=tid8, staging=staging,
                               repo=BRAIN_REPO)
            + charter
            + "\n\n---\n\n# The task record (task %s)\n\n%s\n"
            % (tid8, detail.get("body") or "(no body)"))
    fd, pack_path = tempfile.mkstemp(prefix="dreamer-pack-", suffix=".md")
    with os.fdopen(fd, "w") as fh:
        fh.write(pack)

    author_log = os.path.join(RUN_LOG_DIR, "author-%s.out" % tid8)
    t0 = time.time()
    res = subprocess.run(
        [ENGINE, "run", "--harness", "claude-code", "--role", "author",
         "--prompt-file", pack_path, "--cwd", staging,
         "--out", author_log, "--extra", "--model", "opus"],
        capture_output=True, text=True, timeout=1800)
    os.unlink(pack_path)
    outcome["author_seconds"] = int(time.time() - t0)
    outcome["author_log"] = author_log
    if res.returncode != 0:
        # Release the claim so a later run (or a human) retries cleanly.
        subprocess.run([ENGINE, "task", "release", tid, "--by", AUTHOR,
                        "--note", "author run failed; see %s" % author_log],
                       capture_output=True, text=True)
        outcome["result"] = "author-failed"
        return outcome

    # What did the author do? in-review = proposed a change; done /
    # needs-input = closed without one (both legitimate).
    detail = engine_json(["task", "show", tid])
    outcome["task_status"] = detail["status"]
    if detail["status"] != "in-review":
        outcome["result"] = "closed-without-change"
        return outcome

    change_id = None
    for e in reversed(detail.get("events") or []):
        if e.get("action") == "change-proposed":
            change_id = e["detail"].get("change")
            break
    if not change_id:
        outcome["result"] = "in-review-but-no-change-event"
        return outcome
    outcome["change"] = change_id[:8]

    t0 = time.time()
    res = subprocess.run(
        [ENGINE, "mr", "review-run", change_id, "--harness", "codex",
         "--by", REVIEWER],
        capture_output=True, text=True, timeout=1800)
    outcome["review_seconds"] = int(time.time() - t0)
    outcome["result"] = ("proposed-and-reviewed" if res.returncode == 0
                         else "proposed-review-failed")
    return outcome


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gate", action="store_true",
                    help="exit 0 iff dispatchable work exists")
    ap.add_argument("--max", type=int, default=1,
                    help="tasks to dispatch this run (default 1)")
    args = ap.parse_args()

    todo = dispatchable_tasks()
    if args.gate:
        print("%d unclaimed consolidation task(s) awaiting an author."
              % len(todo))
        for t in todo[:5]:
            print("  %s %s" % (t["id"][:8], t["title"][:70]))
        sys.exit(0 if todo else 1)

    results = [dispatch_one(t) for t in todo[:args.max]]
    print(json.dumps({"dispatched": len(results), "results": results},
                     indent=1))
    if any(r.get("result", "").endswith("failed") for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
