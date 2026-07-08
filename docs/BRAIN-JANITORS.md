# Building brain janitors — a field guide

Written 2026-07-08, at the end of the session that took the dreamer
from a hand-driven prototype to a production pipeline on the engine
work system. This is the distilled experience: what the consolidation
janitor taught us, written down so the NEXT janitor (fact checker,
todo-staleness verifier, consistency checker, metadata sweeper) is a
day's work instead of a season's. Plain language throughout; written
for any model lineage and for humans, cold.

The companion documents: the engine's `design/DESIGN.md` section 9
(what a janitor is) and `design/CHANGE-KINDS.md` (how non-git changes
flow); this repo's `tools/consolidation/` (the worked example);
`RULES.md` (normative constraints).

## 1. The architecture you are plugging into

A brain janitor is not a program that fixes brain. It is a pipeline of
small parts, each with one job, and MOST of the parts already exist:

    detection (deterministic, zero tokens)
      → engine tasks (one per item, full payloads in the body)
        → authoring run (a judging agent, online, evidence-gathering)
          → cross-lineage review (offline, pack-only)
            → human adjudication (the dashboard)
              → warden apply (deterministic, on the brain locus)

The parts a NEW janitor actually needs to build are usually just the
first one (a detection query) and a charter (what the author should
judge and how). Everything downstream — the review flow, the human
gate, the apply handler, the audit trail — is shared machinery.
Resist the urge to build a new pipeline; the dreamer's whole
migration was about deleting one.

Concretely, the shared pieces and where they live:

- Task filing + dedup memory: the engine queue. A filed task carries
  `brain:<entry-id>` sources; those sources ARE the memory of what was
  already asked (exact-set match = never re-ask; overlap with open
  work = wait). Denials stay denied this way with zero extra state.
- Mutations: the `brain-change` kind. Its staging JSON carries an
  `action`; `brain apply-change` dispatches. Today's actions:
  merge-supersede, update-status, fix-metadata. A new janitor that
  can express its fixes in these three needs NO new apply code.
- Non-mutating outcomes: receipt DONE with a comment (nothing wrong),
  file an engine flag (found something for a human), receipt
  NEEDS_INPUT (found something but cannot resolve it).
- Review: `engine mr review-run` + the brain rubric section
  (`kinds.json` points at it). Extend the rubric when you add an
  action; do not fork it.
- Pacing and dispatch: `tools/consolidation/dreamer-dispatch.py`
  claims one task per warden tick, runs author then reviewer. A new
  janitor whose tasks look like consolidation tasks (title marker,
  full payload body) can reuse it outright or copy its shape.

## 2. Principles, each one earned

These are not aesthetics. Every one of these was bought with a real
failure or a real near-miss. When you violate one, you will usually
rediscover why it exists.

**Deterministic first, tokens last.** The gate that wakes a janitor
must cost nothing (SQL, file stat, exit code). But go further: any
judgment that CAN be a deterministic screen should be one. The
time-series screens in discover.py (shared excluded tag; identical
titles after date removal across DISTINCT dates) silently killed 17
of 48 clusters that would each have cost an author run to conclude
"no-action, it's a daily report". The charter's time-series hard rule
still exists — as the backstop, not the mechanism.

**Exclude processed state from detection, or process it forever.**
v1 discovery forgot to exclude superseded entries; every applied
merge would have been rediscovered every night at cosine ~0.99
against its own replacement. Whatever your janitor marks as handled
(superseded, done, flagged) must be invisible to its next detection
pass. Test this by running detection twice around one applied fix.

**Same-signal-different-date is a series; same-date is a duplicate.**
More generally: your screens need a concept of "this repeats
legitimately" vs "this was recorded twice". Get the polarity right —
the first is skipped, the second is exactly the work.

**The detector and the judge are different programs.** Detection
enumerates candidates cheaply and over-inclusively; the author judges
ONE candidate with full attention and tools. Never let the detector
judge (it will be wrong silently) and never let the judge enumerate
(it will burn context and miss things). This is the split-janitor
design; the monolithic form is acceptable only as a first prototype.

**One item per authoring run.** Context isolation is not a luxury:
an author judging cluster 7 with clusters 1–6 in its context window
carries residue — anchoring, fatigue-shaped shortcuts, cross-item
confusion. One task, one staging directory, one run, one receipt.

**Two confidences, never multiplied.** issue_confidence (something
is wrong) and resolution_confidence (my fix is right) are different
facts. "0.9 / 0.3" is a well-diagnosed problem with an uncertain fix
— a defer-to-human, which is a VALUED outcome, not a failure. A
single blended score hides exactly the cases the human most needs to
see.

**Evidence must be cited, and time is never evidence.** The canonical
trap is closing "prepare for the exam on the 23rd" because it is the
27th. Every status change requires a pointer to something checkable:
an entry id, a commit hash, a file, a message. `brain apply-change`
enforces this at apply time too — defense in depth, because a rule
that lives only in a prompt is a rule that eventually gets ignored.

**Evidence must be VISIBLE to a shell-less reviewer.** The reviewer
runs offline, pack-only (on this host, doubly so — the sandbox cannot
even exec). An author's claim "I searched and found no fourth entry"
is unverifiable unless the trimmed raw output is pasted into the
delta. For a reviewer, evidence not in the pack does not exist.

**Cross-lineage review earns its cost.** The Codex reviewer caught,
among others: a merged body whose open-action phrasing contradicted
its done status; silent synthesis (two source claims fused into one
stronger claim neither made); a pronoun ambiguity in a title; a
metadata change smuggled in as part of a "pure dedup". Different
priors notice different failure modes. Do not review with the
author's own lineage to save a subscription call.

**Preserve, never launder.** Correction signal ("X — actually no,
Y") IS the content. Editorial framing (added sub-headers, bullet
labels) is re-classification wearing clarity's clothes. Superseded
entries stay in the database — supersession is the rollback story.
And every field of a replacement entry is set explicitly, because
absent fields become NULLs, and that is how authorship gets erased.

**House style for completed work** (adjudicator's ruling): completion
evidence is cited inline in the entry body — the audit trail is
invisible to future readers; the body is what they see. A done entry
must not read like a standing instruction; rephrase leftover
open-action language to past tense.

**Absolute paths in anything recorded for later.** The apply step
runs hours later, from a different working directory, under a daemon.
A relative staging path stranded a real change for three land
attempts. The engine now canonicalizes at propose time; keep the
charter's instruction anyway.

**Cap everything; fail loudly; release what you claimed.** Detection
files at most N tasks per run (a bad threshold must not flood the
queue). Dispatch takes one task per tick (a backlog drains gently
instead of stampeding subscriptions). A failed author run releases
its claim with a pointer to the log. Wardens on other machines must
skip your janitor harmlessly (self-guarding gates), not crash on it.

**Shakedown before autonomy — the graduation ladder.** The dreamer
ran four hand-driven rounds before its janitors were registered:
(1) a clean merge, (2) a no-action closure, (3) a merge with
correction signal that failed to land twice for infrastructure
reasons, (4) a full return→fix→re-review→land cycle. Every leg of the
flow was walked manually, every failure diagnosed, THEN the machinery
got the keys — still with the human gate on every mutation. Budget
the same ladder for every new janitor: manual rounds, one item at a
time, until the failure modes stop surprising you.

**The human gate is cheap or it is fake.** The dashboard, rendered
markdown, labeled scores, full source payloads next to the proposal —
all of that exists because a 500-line markdown file went unread for
29 days. If your janitor's proposals are exhausting to adjudicate,
the human will click through them blindly, and the gate becomes
theater. Design the delta rendering FOR the tired human.

## 3. The recipe for janitor N+1

1. **Define the item.** What is the unit of work? (A cluster; a stale
   todo; a fact older than X with a checkable claim; an entry with
   missing metadata.) One item = one engine task = one authoring run.

2. **Write the detection query.** Deterministic, read-only, cheap.
   Decide the exclusions: what marks an item as already-handled?
   (Queue reconciliation via `brain:<id>` sources gives you
   already-asked and overlap-with-open for free — copy
   `discover.py`'s `reconcile()`.) Add the legitimate-repetition
   screens your domain needs. Give it `--gate`, `--dry-run`, and
   `--file-tasks --max-tasks N` modes.

3. **Choose the outcome vocabulary.** Map your fixes onto the
   existing actions first (merge-supersede / update-status /
   fix-metadata cover more than you expect). A genuinely new action
   means extending, in one commit: the staging schema in the author
   charter, `brain apply-change`'s dispatch (with its guards), and
   the review rubric. Non-mutating outcomes (DONE-with-comment, flag,
   NEEDS_INPUT) need nothing.

4. **Write the charter.** Start from `author-charter.md`'s skeleton:
   what the item is, the outcomes, the hard rules (inherit the
   general ones; add domain-specific ones ONLY for failure modes you
   have actually seen or can name concretely), evidence-gathering
   instructions, the two confidences, the staging format, the exact
   propose command. Keep it small: many narrow janitors beat one
   broad one.

5. **Extend the rubric** with a section for your item type: what
   information loss means here, what over-confidence looks like, what
   the reviewer should refuse to let through.

6. **Task nutrition.** The task body must carry the FULL item payload
   (entry bodies, not ids alone) plus a pointer to the charter. The
   author is online, but the reviewer sees only the pack — and a
   different-lineage author shares none of your unstated context.

7. **Shakedown manually** (the ladder above), using
   `dreamer-dispatch.py`'s claim→pack→run→review sequence by hand or
   copied. Iterate the charter on real outputs — the dreamer's
   charter went through four review-driven revisions before it
   stabilized, and every one of its hard rules came from a round.

8. **Register the janitors** (detection + dispatch, or reuse
   brain-dispatch if your tasks match its marker): self-guarding
   gates (`cd <brain repo> || exit 1`), `cwd='~'`, cheap charter
   model (the charter agent only babysits a script), caps in place.
   Watch the first unattended nights on the dashboard ledger.

## 4. The candidate janitors, sketched

**Stale-todo verifier** (the most-wanted). Item: an active todo older
than N days. Detection: pure SQL — no clustering; add
`last_completion_check_at` with exponential backoff so the same todo
is not re-hunted nightly (schema todo 26f3add0 also wants `due_at`
separated from `expires_at`). Author: hunt completion evidence wide
(brain, git log of the referenced repos, mail) and propose
update-status with citations, or receipt DONE ("still genuinely
open"), or defer. Outcome vocabulary: entirely covered by
update-status. This janitor is 80% detection query + charter; the
pipeline exists.

**Metadata sweeper.** Item: an entry with missing/implausible
project, empty tags, or dangling entity_refs. Detection: SQL.
Missing embeddings need NO janitor at all — `brain embed --all
--missing` is deterministic; put it in a gate-guarded cron or the
warden. The judgment cases (which project does this belong to?) map
onto fix-metadata. Cheap model may suffice for the author; the
change is small and reviewable at a glance. (Brain todo 11817e3c.)

**Fact checker.** Item: a fact whose claim is checkable against
reality reachable from this host (a config file, a service, a URL, a
path). Detection is the hard part: identifying CHECKABLE facts is
itself a judgment — start with a deterministic heuristic (facts whose
bodies contain paths/ports/hostnames) and let precision grow.
Outcomes: fix via supersede (merge-supersede with one source works as
"revise"; or extend with a dedicated `revise` action if that reads
too much like a hack), flag-contradiction when reality disagrees but
the fix is unclear, defer when checking is impossible. Expect a high
defer rate at first; that is the janitor working, not failing.

**Internal-consistency checker.** Item: a pair/neighborhood of
entries making incompatible claims. Detection: embedding neighbors
(lower threshold than consolidation, ~0.80–0.90) minus the
consolidation-eligible band, plus entity_refs overlap as a second
axis. This one is genuinely hard — most "contradictions" are
temporal succession (X was true, then Y). The charter must lean on
timestamps and prefer flag-contradiction/defer heavily. Build it
LAST, after the easier janitors have hardened the shared machinery
and the human has calibration data on the authors' judgment.

**Entity extractor.** Item: bare facts that should be entity
attributes ("X is Y's pet"). The dreamer currently defers these to a
human; a dedicated janitor could propose the conversion (create/
update entity + supersede the fact). Needs a new action (touches
entities, not just entries) — the first real test of extending
apply-change. The dreamer's defer-to-human comments are its
ready-made backlog.

**Dead-reference checker.** entity_refs pointing at slugs that do
not exist, superseded_by chains ending in soft-deleted entries,
project slugs used once. Fully deterministic detection AND largely
deterministic fixing — much of this may need no LLM at all. Do it as
a report-only script first; promote findings that recur into
fix-metadata flows.

## 5. What to watch as this grows

- **Cost discipline.** Every janitor author run is a subscription
  call. The gates keep idle nights free, the caps keep busy nights
  bounded, but N janitors × nightly is real money: prefer weekly
  cadences for low-urgency janitors (staleness does not rot in a
  day), and revisit model choices — judging a metadata fix does not
  need the taste-heavy model that consolidation merges deserve.
- **Calibration ledger.** The adjudication history (verdicts vs the
  authors' confidence numbers, reviewers' agreement scores) is
  accumulating in the engine database. When there is enough of it,
  it answers the auto-approve question ("above what confidence, for
  which actions, has the human never overridden?") with data instead
  of vibes. Do not enable auto-approve before that ledger says so;
  the engine's own posture — no new machinery before the simple
  version has been operated for a while — applies doubly to removing
  the human.
- **The charter is living law.** Every human return, every reviewer
  finding that repeats, every "huh, that's the third time" belongs
  either in a deterministic screen (preferred) or a charter rule
  (backstop). Tonight's charter is v4-ish; expect v10. Keep each
  rule's origin visible in commit messages — a rule whose failure
  story is lost gets deleted by a well-meaning simplifier and then
  re-earned the hard way.
- **Multi-brain remains deliberately out of scope.** Everything here
  runs on zeresh_brain only. fay_brain and david_brain need an
  adjudication story (whose dashboard? whose verdict?) before any
  janitor touches them. Do not quietly widen a detection query's
  `--db`.

## 6. Where everything is

- Worked example: `tools/consolidation/` — discover.py (detection),
  dreamer-dispatch.py (dispatch), author-charter.md, review-rubric-
  brain.md, janitor-discovery.md + janitor-dispatch.md (registered
  charters), orchestrator.py (frozen v1, reference only).
- Apply handler: `brain apply-change` in brain-cli.py; N-to-1 merges
  via `brain merge-entries`.
- Engine side: `../engine` — kinds.json (brain-change registration),
  DESIGN.md §9 (janitors), CHANGE-KINDS.md (non-git changes),
  OPERATORS-MANUAL.md (locus onboarding, warden, troubleshooting).
- Staging: `~/.local/state/brain-dreamer/changes/<task-prefix>/`;
  author logs: `~/.local/state/brain-dreamer/runs/`.
- Brain entries: architecture decision `aac234dd`, runbook
  `0b4d965d`.
- Identities: authors `brain-author:<model>`, reviewers
  `brain-reviewer:<model>`, machinery `janitor:<name>`, maintenance
  `brain-steward`.
