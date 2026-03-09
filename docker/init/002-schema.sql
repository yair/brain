-- Zeresh Brain Schema

-- Core memory entries
CREATE TABLE entries (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    
    -- Classification
    kind        TEXT NOT NULL,       -- decision, insight, fact, debrief, todo, observation, preference
    source      TEXT NOT NULL,       -- zeresh, jay, claude-code, sonnet-worker, triage, fay-agent
    session_id  TEXT,
    
    -- Content
    title       TEXT NOT NULL,
    body        TEXT NOT NULL,
    tags        TEXT[] DEFAULT '{}',
    
    -- Relations
    project     TEXT,
    entity_refs TEXT[] DEFAULT '{}',
    
    -- Search
    embedding   vector(768),        -- Gemini text-embedding-004
    tsv         tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('english', coalesce(title, '')), 'A') ||
        setweight(to_tsvector('english', coalesce(body, '')), 'B')
    ) STORED,
    
    -- Lifecycle
    confidence  FLOAT DEFAULT 1.0,
    superseded_by UUID REFERENCES entries(id),
    expires_at  TIMESTAMPTZ,
    status      TEXT DEFAULT 'active'  -- active, superseded, expired, deleted
);

-- Indexes
CREATE INDEX idx_entries_kind ON entries(kind);
CREATE INDEX idx_entries_project ON entries(project);
CREATE INDEX idx_entries_source ON entries(source);
CREATE INDEX idx_entries_status ON entries(status);
CREATE INDEX idx_entries_created ON entries(created_at DESC);
CREATE INDEX idx_entries_tags ON entries USING gin(tags);
CREATE INDEX idx_entries_entity_refs ON entries USING gin(entity_refs);
CREATE INDEX idx_entries_tsv ON entries USING gin(tsv);
CREATE INDEX idx_entries_embedding ON entries USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

-- People, projects, tools
CREATE TABLE entities (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,       -- person, project, client, tool, place
    name        TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Calendar events
CREATE TABLE events (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title       TEXT NOT NULL,
    starts_at   TIMESTAMPTZ NOT NULL,
    ends_at     TIMESTAMPTZ,
    location    TEXT,
    attendees   TEXT[] DEFAULT '{}',
    notes       TEXT,
    source      TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_events_starts ON events(starts_at);

-- Location tracking (hypertable for TimescaleDB)
CREATE TABLE location (
    timestamp   TIMESTAMPTZ NOT NULL,
    source      TEXT NOT NULL,
    lat         DOUBLE PRECISION,
    lon         DOUBLE PRECISION,
    accuracy_m  FLOAT,
    label       TEXT,
    raw         JSONB
);

SELECT create_hypertable('location', 'timestamp');

-- Telemetry (future: phone sensors, UPS, etc.)
CREATE TABLE telemetry (
    timestamp   TIMESTAMPTZ NOT NULL,
    source      TEXT NOT NULL,       -- phone/battery, phone/screen, ups/status, etc.
    value       JSONB NOT NULL,
    label       TEXT
);

SELECT create_hypertable('telemetry', 'timestamp');

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER entries_updated_at BEFORE UPDATE ON entries
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
CREATE TRIGGER entities_updated_at BEFORE UPDATE ON entities
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
