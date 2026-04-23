-- 002-session-key-rename.sql
--
-- Renames recall_log.session_id → session_key and updates log_recall()
-- to match. Aligns with OC's sessionKey convention: a stable, semantic
-- scene identifier like 'agent:main:telegram' or 'code:brain-repo',
-- NOT a raw UUID. If UUID correlation ever matters, add a separate
-- session_uuid column in a later migration.
--
-- Apply as the superuser role (brain):
--   docker exec -i brain-db psql -U brain -d <dbname> < 002-session-key-rename.sql


-- ── rename column (idempotent) ──────────────────────────────────────

DO $$ BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'recall_log' AND column_name = 'session_id'
    ) AND NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'recall_log' AND column_name = 'session_key'
    ) THEN
        ALTER TABLE recall_log RENAME COLUMN session_id TO session_key;
    END IF;
END $$;


-- ── rebuild log_recall with session_key parameter ───────────────────

-- We DROP the old signature and CREATE fresh; CREATE OR REPLACE cannot
-- change parameter names of an existing function.
DROP FUNCTION IF EXISTS log_recall(UUID, TEXT, INT, FLOAT, TEXT, TEXT, TEXT);

CREATE FUNCTION log_recall(
    p_entry_id    UUID,
    p_query       TEXT,
    p_rank        INT,
    p_score       FLOAT,
    p_session_key TEXT,
    p_source      TEXT,
    p_context     TEXT
) RETURNS VOID
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
BEGIN
    INSERT INTO recall_log (entry_id, query, rank, score, session_key, source, context)
    VALUES (p_entry_id, p_query, p_rank, p_score, p_session_key, p_source, p_context);
END;
$$;

ALTER FUNCTION log_recall(UUID, TEXT, INT, FLOAT, TEXT, TEXT, TEXT)
    OWNER TO brain_dream;

REVOKE ALL ON FUNCTION log_recall(UUID, TEXT, INT, FLOAT, TEXT, TEXT, TEXT)
    FROM PUBLIC;

GRANT EXECUTE ON FUNCTION log_recall(UUID, TEXT, INT, FLOAT, TEXT, TEXT, TEXT)
    TO brain_cli, brain_dream;
