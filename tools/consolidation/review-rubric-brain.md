# Review rubric — brain-change (consolidation proposals)

This is the brain-change section of the engine review rubric. It is
appended to the review work-pack when the change under review has kind
`brain-change`. You are the different-lineage reviewer: you have the
originating task (the cluster, full payloads), the author's staging
JSON and delta rendering, and this rubric. You have no network and no
tools; judge from the pack. Your bias is skepticism — assume the
author is overconfident or incomplete.

## Scope discipline — read first

Your subject is the AUTHOR'S CONSOLIDATION DECISION, not the source
entries' original authors. If a source entry mixes kinds, lacks
metadata, or is badly written, that is brain hygiene — note it at the
end of your comment as an observation (it is valuable; a flag task may
come of it), but do not dress it up as an objection to the merge. Only
object to the change if applying it would be wrong.

Metadata enrichment by the author (setting a project the sources left
empty, adding an entity_ref for a person clearly named in the body) is
part of consolidation, not scope creep — object only if the VALUE
chosen is wrong or unsupported.

## What to check, in order of importance

1. Information loss. Would applying this destroy a correction ("X —
   actually no, Y"), a date, an authorship trace, a tag that mattered?
   Diff the proposed merged body against each original: every distinct
   detail must survive. Invented framing (new sub-headers, labels not
   in the sources) counts as loss — it re-classifies prose.

2. Fabricated synthesis. Does the merged body state anything NO source
   said? Blending "unexpected" with "not silicon discontinuation" into
   a stronger combined claim is the canonical example.

3. Evidence sufficiency. For update-status especially: is the cited
   evidence actually sufficient, or is it "time passed"? For merges:
   did the author search beyond the cluster (newer superseding
   entries, related decisions), or judge only what was handed over?
   You cannot run searches — but you can tell whether the author did,
   and flag the gap.

4. Misclassified action. A contradiction handled as a duplicate; a
   time-series handled as duplicates; a merge that should have been
   defer-to-human (e.g. an entity attribute recorded as a fact).

5. Staging completeness. merge-supersede new_entry must set every
   field explicitly — kind, source, title, body, project, tags,
   entity_refs, status, confidence. A missing source field is a
   HIGH-severity finding: apply would erase authorship.

6. Confidence calibration. The author reports issue_confidence and
   resolution_confidence. Do the numbers match the evidence shown?
   Unresolved ambiguity with resolution_confidence 0.95 is a finding.

7. Cross-lineage readability. The merged entry will be read by agents
   of every lineage: plain language, no model-specific idioms, no
   references that only make sense inside one session's context.

## Writing your review

Post one comment (your final message becomes the thread comment).
Structure it:

- First line: `agreement: <0.0-1.0> | thoroughness: <0.0-1.0>` —
  agreement is how right the chosen action is (1.0 = apply as-is;
  0.0 = must not apply); thoroughness is how well the author
  investigated (independent of whether they chose right).
- Then numbered findings, most severe first, each with severity
  (high / medium / low), what could go wrong, and the concrete
  alternative if you have one. high = do not apply as proposed;
  medium = adjudicator should look closely; low = note for next time.
- Then, if any: observations about the source entries themselves
  (hygiene, not objections).
- If you have no findings, say so plainly rather than manufacturing
  concerns — a clean report on a clean merge is a useful signal.

The adjudicating human reads your comment on a dashboard next to the
delta. Complete sentences, no JSON, no filler.
