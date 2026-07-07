# RULES — the brain repository

Normative for this repo. Brain is the shared persistent-memory layer
for a human and their AI agents: a public repository (MIT) whose
deployments are private. That split drives most rules here.

## Privacy — the load-bearing rule

This repo is PUBLIC. Nothing committed may contain personal data:
no human names, no agent pet-names, no employer/client/project names
from the private deployments, no email addresses, no local hostnames
or filesystem paths that identify a person or machine. This covers
code, prompts, docs, comments, and commit messages alike.

Generated consolidation artifacts (`consolidation-*.md`, staging
directories) contain live brain content and are gitignored — never
force-add them. When writing examples, invent neutral ones ("alice",
"my-project"); when a real incident motivates a rule, describe the
pattern, not the people.

## Database discipline

- Clients connect as `brain_cli`; dreaming infrastructure as
  `brain_dream`; `brain` (superuser) is admin/break-glass only. New
  capabilities get the narrowest role that works.
- Schema changes are idempotent migration files under
  `docker/migrations/`, numbered, applied per-database (one Postgres
  cluster hosts several brains). A migration that has shipped is never
  edited — write the next one.
- SECURITY DEFINER functions that enforce a privilege boundary must be
  `LANGUAGE plpgsql` (the planner inlines trivial `sql`-language
  functions into the caller's privilege context, silently bypassing
  the boundary) and must pin `search_path`.
- Entries are never hard-deleted by tooling: `expires_at` is the
  soft-delete, `superseded_by` the revision chain. Anything that would
  destroy information needs a human decision.

## Language and lineage

Everything an agent may read cold — prompts, charters, rubrics, docs,
task and entry bodies — is written in plain, complete sentences,
readable by any model lineage and by humans. No shorthand, no
model-specific idioms, no references that only resolve inside one
session's context.

## Work system

Substantial changes to this repo go through the engine work system
(task, review by a different lineage, human adjudication). Brain-
content mutations proposed by the dreaming pipeline follow
`tools/consolidation/author-charter.md` — its hard rules (no deletion,
no todo closure without cited evidence, no merging time-series, no
destroying correction signal) are normative and exist because each was
earned. Commit messages are imperative, explain why, and may reference
engine task ids (traceability outweighs opacity in a repo whose issue
tracker is the engine queue).
