-- 001-dreaming-roles.sql
--
-- Introduces the three-role model for Brain:
--   brain        - superuser (admin, break-glass, table owner). Unchanged.
--   brain_cli    - what CLI/MCP/skill connect as. Exactly the surface those
--                  clients need. No superuser, no bypass.
--   brain_dream  - what the dreaming daemon connects as. Owns dreaming-
--                  internal tables and the log_recall() function.
--
-- Also lands supporting changes that all three roles need to co-exist:
--   - recall_log table (owned by brain_dream)
--   - log_recall() SECURITY DEFINER function (brain_cli's only write path
--     into recall_log)
--   - deleted_at columns on entities + events (soft-delete)
--   - ALTER DEFAULT PRIVILEGES so future brain_dream-owned tables are
--     automatically invisible to brain_cli.
--
-- This file is idempotent. It can be applied to zeresh_brain,
-- fay's brain, and david's brain with the same content. Per-instance
-- passwords are set separately via ALTER ROLE (not committed).
--
-- Apply as the superuser role (brain):
--   docker exec -i brain-db psql -U brain -d <dbname> < 001-dreaming-roles.sql
--
-- After applying, set passwords out-of-band:
--   ALTER ROLE brain_cli   PASSWORD '<per-instance-cli>';
--   ALTER ROLE brain_dream PASSWORD '<per-instance-dream>';


-- ── roles ────────────────────────────────────────────────────────────

DO $$ BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'brain_cli') THEN
        CREATE ROLE brain_cli LOGIN;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'brain_dream') THEN
        CREATE ROLE brain_dream LOGIN;
    END IF;
END $$;


-- ── schema additions ────────────────────────────────────────────────

ALTER TABLE entities ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;
ALTER TABLE events   ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ;

-- Partial indexes: keep the index small by only covering live rows.
CREATE INDEX IF NOT EXISTS idx_entities_active ON entities(id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_events_active   ON events(starts_at) WHERE deleted_at IS NULL;


-- ── dreaming tables ─────────────────────────────────────────────────

-- recall_log: every hit from brain_search (and CLI/skill equivalents) is
-- logged here, one row per entry returned. Feeds the 6-signal scoring
-- used by Light/REM/Deep phases.
CREATE TABLE IF NOT EXISTS recall_log (
    id          BIGSERIAL PRIMARY KEY,
    entry_id    UUID NOT NULL REFERENCES entries(id),
    query       TEXT NOT NULL,
    rank        INT,
    score       FLOAT,
    session_id  TEXT,
    source      TEXT,
    context     TEXT,
    recalled_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_recall_log_entry ON recall_log(entry_id);
CREATE INDEX IF NOT EXISTS idx_recall_log_time  ON recall_log(recalled_at DESC);

ALTER TABLE recall_log OWNER TO brain_dream;
ALTER SEQUENCE recall_log_id_seq OWNER TO brain_dream;


-- ── log_recall: brain_cli's only write path into recall_log ─────────

-- SECURITY DEFINER means this function runs with the privileges of its
-- owner (brain_dream), so it can INSERT into recall_log even though the
-- caller (brain_cli) has no direct rights on that table. The explicit
-- search_path prevents schema-injection tricks.
--
-- We use LANGUAGE plpgsql (not sql) deliberately: the planner inlines
-- trivial SQL-language functions at parse time, which evaluates the body
-- with the caller's privileges and silently bypasses SECURITY DEFINER.
-- plpgsql functions are never inlined, so the privilege switch sticks.
CREATE OR REPLACE FUNCTION log_recall(
    p_entry_id   UUID,
    p_query      TEXT,
    p_rank       INT,
    p_score      FLOAT,
    p_session_id TEXT,
    p_source     TEXT,
    p_context    TEXT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    INSERT INTO recall_log (entry_id, query, rank, score, session_id, source, context)
    VALUES (p_entry_id, p_query, p_rank, p_score, p_session_id, p_source, p_context);
END;
$$;

ALTER FUNCTION log_recall(UUID, TEXT, INT, FLOAT, TEXT, TEXT, TEXT)
    OWNER TO brain_dream;

REVOKE ALL ON FUNCTION log_recall(UUID, TEXT, INT, FLOAT, TEXT, TEXT, TEXT)
    FROM PUBLIC;

GRANT EXECUTE ON FUNCTION log_recall(UUID, TEXT, INT, FLOAT, TEXT, TEXT, TEXT)
    TO brain_cli, brain_dream;


-- ── grants: brain_cli ───────────────────────────────────────────────

-- Mirror of what brain-cli.py actually does, plus the entity/event
-- management surface we're adding in this same wave of changes.

GRANT SELECT, INSERT, UPDATE ON entries    TO brain_cli;
GRANT SELECT, INSERT, UPDATE ON entities   TO brain_cli;
GRANT SELECT, INSERT, UPDATE ON events     TO brain_cli;
GRANT SELECT, INSERT           ON location   TO brain_cli;
GRANT SELECT, INSERT           ON retrievals TO brain_cli;
GRANT SELECT, INSERT           ON access_log TO brain_cli;

-- access_log.id is actually BIGSERIAL in the live schema (docker/init/002-schema.sql
-- claims UUID but the live table drifted). brain_cli and brain_dream both insert
-- into access_log, so both need USAGE on the sequence.
GRANT USAGE ON SEQUENCE access_log_id_seq TO brain_cli, brain_dream;
-- telemetry: deliberately NOT granted to brain_cli. Written by Plexus
-- as admin today; will get its own role when Plexus is re-architectured.

-- No direct access to recall_log — only via log_recall().
-- Default privileges below ensure future brain_dream tables stay hidden.


-- ── grants: brain_dream ─────────────────────────────────────────────

-- Reads everything (needs all signals to score entries).
GRANT SELECT ON entries, entities, events, location,
                retrievals, access_log, telemetry TO brain_dream;

-- Writes to entries (promote / supersede / expire / confidence updates)
-- and access_log (log dreaming's own provenance when it touches entries).
GRANT INSERT, UPDATE ON entries    TO brain_dream;
GRANT INSERT          ON access_log TO brain_dream;


-- ── default privileges: staging stays invisible ─────────────────────

-- When brain_dream later creates dream_candidates (Phase 1) or similar
-- internal tables, brain_cli must not see them. These defaults apply
-- only to tables created *after* this statement runs, by brain_dream.
ALTER DEFAULT PRIVILEGES FOR ROLE brain_dream IN SCHEMA public
    REVOKE ALL ON TABLES FROM brain_cli, PUBLIC;

-- Sanity: on the tables brain_dream owns right now (recall_log), make
-- sure brain_cli has nothing. (Postgres default is no grant anyway,
-- but explicit is better for auditing.)
REVOKE ALL ON recall_log FROM brain_cli, PUBLIC;


-- ── future plexus role ──────────────────────────────────────────────

-- Plexus currently writes telemetry + location as the brain superuser.
-- That project is slated for re-architecture into its own database, so
-- we do NOT create a brain_plexus role here. When Plexus lands in its
-- new home, revoke brain's ambient access from that side.
