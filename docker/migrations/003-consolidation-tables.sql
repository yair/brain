-- 003-consolidation-tables.sql
--
-- Tables for the consolidation pipeline (Yoko proposes, Ringo critiques,
-- human reviews via CLI/markdown, decisions auto-apply on approve).
--
-- Apply as the superuser role (brain):
--   docker exec -i brain-db psql -U brain -d <dbname> < 003-consolidation-tables.sql
--
-- Tables:
--   consolidation_proposals  one row per (cluster, dream-cycle) — both
--                            Yoko's full proposal and Ringo's full
--                            review live as JSONB on this row. Hot
--                            fields (action, confidences, status) are
--                            hoisted as columns for filtering.
--
--   consolidation_log        append-only audit of every status
--                            transition. Wins over a JSONB history
--                            column when we want to query "all
--                            proposals approved in the last week" etc.
--
-- Ownership: brain_dream owns both. brain_cli reads everything and can
-- write the human-decision fields + insert log rows. Apply-engine runs
-- as brain_cli (CLI process), so brain_cli also writes applied_at /
-- apply_error / superseded_by.


-- ── status enum ─────────────────────────────────────────────────────

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'consolidation_status') THEN
        CREATE TYPE consolidation_status AS ENUM (
            'pending',         -- awaiting human review
            'auto_approved',   -- confidence above threshold; applied without human
                               -- (future: threshold currently >1, never fires)
            'approved',        -- human said yes
            'denied',          -- human said no
            'deferred',        -- human pushed back; will likely re-propose next cycle
            'escalated',       -- human flagged for deeper investigation
            'applied',         -- the underlying mutation has committed to brain tables
            'stale'            -- a newer proposal on overlapping cluster displaced this one
        );
        ALTER TYPE consolidation_status OWNER TO brain_dream;
    END IF;
END $$;


-- ── consolidation_proposals ────────────────────────────────────────

CREATE TABLE IF NOT EXISTS consolidation_proposals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The cluster Yoko reviewed — entry UUIDs from the entries table.
    -- GIN-indexed for fast overlap queries when staling old proposals.
    cluster_entry_ids UUID[] NOT NULL,

    -- Full proposals as JSONB. Schemas described in
    -- tools/consolidation/{yoko,ringo}-system.md.
    yoko_proposal JSONB NOT NULL,
    ringo_review  JSONB NOT NULL,

    -- Hoisted hot fields for filtering / ranking.
    action TEXT NOT NULL,         -- merge-supersede | update-status | fix-metadata
                                  -- | flag-contradiction | no-action | defer-to-human
    issue_confidence       REAL,
    resolution_confidence  REAL,
    agreement              REAL,
    thoroughness           REAL,

    -- Lifecycle.
    status consolidation_status NOT NULL DEFAULT 'pending',
    superseded_by UUID REFERENCES consolidation_proposals(id),  -- set when staled

    decided_at    TIMESTAMPTZ,
    decided_by    TEXT,           -- 'cli', 'claude-code', 'human', etc.
    decision_note TEXT,

    applied_at  TIMESTAMPTZ,
    apply_error TEXT,             -- non-null = apply attempted and failed

    -- Provenance.
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    yoko_model  TEXT,             -- e.g. 'claude-opus-4-7'
    ringo_model TEXT              -- e.g. 'claude-sonnet-4-6'
);

ALTER TABLE consolidation_proposals OWNER TO brain_dream;

CREATE INDEX IF NOT EXISTS consolidation_proposals_status_idx
    ON consolidation_proposals (status);

CREATE INDEX IF NOT EXISTS consolidation_proposals_cluster_gin
    ON consolidation_proposals USING GIN (cluster_entry_ids);

CREATE INDEX IF NOT EXISTS consolidation_proposals_created_at_idx
    ON consolidation_proposals (created_at DESC);


-- ── consolidation_log (audit trail) ────────────────────────────────

CREATE TABLE IF NOT EXISTS consolidation_log (
    id BIGSERIAL PRIMARY KEY,
    proposal_id UUID NOT NULL REFERENCES consolidation_proposals(id) ON DELETE CASCADE,
    from_status consolidation_status,
    to_status   consolidation_status NOT NULL,
    actor       TEXT NOT NULL,    -- 'orchestrator', 'cli', 'apply-engine', 'jay'
    note        TEXT,
    payload     JSONB,            -- e.g. apply diff, error details
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE consolidation_log OWNER TO brain_dream;

CREATE INDEX IF NOT EXISTS consolidation_log_proposal_id_idx
    ON consolidation_log (proposal_id);

CREATE INDEX IF NOT EXISTS consolidation_log_created_at_idx
    ON consolidation_log (created_at DESC);


-- ── grants ─────────────────────────────────────────────────────────

-- brain_cli reads proposals and the log freely.
GRANT SELECT ON consolidation_proposals TO brain_cli;
GRANT SELECT ON consolidation_log TO brain_cli;

-- brain_cli writes only the columns it should: human-decision fields,
-- apply outcome (apply runs as brain_cli), and superseded_by (the
-- orchestrator could conceivably also run with cli creds during dev).
GRANT UPDATE (status, superseded_by,
              decided_at, decided_by, decision_note,
              applied_at, apply_error)
    ON consolidation_proposals TO brain_cli;

-- brain_cli appends to the audit log.
GRANT INSERT ON consolidation_log TO brain_cli;
GRANT USAGE ON SEQUENCE consolidation_log_id_seq TO brain_cli;

-- ALTER DEFAULT PRIVILEGES from migration 001 already grants brain_dream
-- the keys to its own future tables; nothing extra needed here.
