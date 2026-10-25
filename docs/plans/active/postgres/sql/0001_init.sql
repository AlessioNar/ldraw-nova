-- Open Brick Designer: initial PostgreSQL schema (proposal, validated on postgres:18-alpine).
-- Conventions: ids the app already uses stay text (slugs, chat ids, 12-hex ids); times are timestamptz
-- (the repository layer returns float epoch seconds so callers keep working); fluid structure is jsonb.

CREATE TABLE app_setting (                       -- non-secret settings only; keys and endpoints stay in /config
    key         text PRIMARY KEY,
    value       jsonb NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- builds and chats
CREATE TABLE build (                             -- data/builds/<slug>/build.json
    id          text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9-]{0,59}$'),     -- the slug
    title       text NOT NULL,
    source      jsonb NOT NULL DEFAULT '{"kind":"import"}',                      -- {kind, generator?, params?, plan_hash?}
    source_kind text GENERATED ALWAYS AS (source->>'kind') STORED
                CHECK (source_kind IN ('generator','ai','import','editor','empty')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE chat (                              -- data/chats/<id>/chat.json
    id              text PRIMARY KEY CHECK (id ~ '^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$' AND id !~ '^b-'),
    title           text NOT NULL DEFAULT 'New chat',
    llm_model_id    text,
    options         jsonb NOT NULL DEFAULT '{}',
    build_id        text REFERENCES build(id) ON DELETE SET NULL,                -- replaces build.json "chats"
    next_message_id integer NOT NULL DEFAULT 1,                                  -- per-chat id counter, bumped under the row lock
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX chat_updated_idx ON chat (updated_at DESC);
CREATE INDEX chat_build_idx   ON chat (build_id) WHERE build_id IS NOT NULL;

CREATE TABLE message (                           -- messages.jsonl; body is the agent message exactly as today
    chat_id    text NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
    id         integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    body       jsonb NOT NULL,                   -- role, content, tool_calls, _usage, _worker, _editor_publish, ...
    role       text GENERATED ALWAYS AS (body->>'role') STORED,
    PRIMARY KEY (chat_id, id)
);

CREATE TABLE worker_step (                       -- chats/<id>/workers/<worker>.jsonl
    chat_id    text NOT NULL REFERENCES chat(id) ON DELETE CASCADE,
    worker_id  text NOT NULL,
    seq        integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    body       jsonb NOT NULL,
    PRIMARY KEY (chat_id, worker_id, seq)
);

CREATE TABLE model_record (                      -- models.jsonl: a chat's published models AND a build's versions
    id         text PRIMARY KEY,
    chat_id    text REFERENCES chat(id)  ON DELETE CASCADE,
    build_id   text REFERENCES build(id) ON DELETE CASCADE,
    version    integer NOT NULL CHECK (version > 0),
    name       text NOT NULL,
    file       text NOT NULL,                    -- relative to /data, e.g. generated/azure-dart-v2.mpd (the file stays the truth)
    sha256     text,                             -- of the file when recorded; reconcile detects drift
    parts      integer,
    warnings   jsonb NOT NULL DEFAULT '[]',
    source     text,                             -- work-folder file it was published from
    origin     text CHECK (origin IN ('generator','ai','editor','import')),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (num_nonnulls(chat_id, build_id) = 1)
);
CREATE UNIQUE INDEX model_record_build_version ON model_record (build_id, version) WHERE build_id IS NOT NULL;
CREATE UNIQUE INDEX model_record_chat_version  ON model_record (chat_id,  version) WHERE chat_id  IS NOT NULL;

CREATE TABLE build_draft (                       -- data/builds/<slug>/draft.json (the editor session itself stays in files)
    build_id        text PRIMARY KEY REFERENCES build(id) ON DELETE CASCADE,
    base_version    integer,
    base_file       text,
    base_revision   integer NOT NULL DEFAULT 0,
    saved_revision  integer NOT NULL DEFAULT 0,
    base_undo_depth integer NOT NULL DEFAULT 0,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE proposal (                          -- proposals/<id>.json; the .mpd stays a file next to it
    id         text PRIMARY KEY CHECK (id ~ '^[0-9a-f]{12}$'),
    build_id   text NOT NULL REFERENCES build(id) ON DELETE CASCADE,
    chat_id    text REFERENCES chat(id) ON DELETE SET NULL,
    summary    jsonb NOT NULL DEFAULT '{}',      -- {added, removed, moved}
    cost_eur   numeric(10,4) NOT NULL DEFAULT 0,
    warnings   jsonb,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX proposal_build_idx ON proposal (build_id, created_at);

-- ---------------------------------------------------------------- parts collection
CREATE TABLE part_source (                       -- inventories/<id>.json (the original upload <id>.csv stays a file)
    id               text PRIMARY KEY CHECK (id ~ '^[a-z0-9][a-z0-9-]{0,63}$'),
    name             text NOT NULL,
    kind             text NOT NULL DEFAULT 'import' CHECK (kind IN ('import','set','loose')),
    schema_version   integer NOT NULL DEFAULT 2,
    source_filename  text,
    origin           jsonb,                      -- {format, scan, file}
    set_info         jsonb,                      -- {set_num, copies, state}
    import_meta      jsonb,                      -- the old "import" key
    totals           jsonb,
    warnings         jsonb NOT NULL DEFAULT '[]',
    unmapped_parts   jsonb NOT NULL DEFAULT '[]',
    unmapped_colours jsonb NOT NULL DEFAULT '[]',
    csv_file         text,
    rows_hash        text,                       -- sha256 of the sorted rows: cache key for buildable and for the JSON export
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX part_source_name_key ON part_source (lower(name));

CREATE TABLE part_row (                          -- exact rows: LDraw part number + LDraw colour code
    source_id  text NOT NULL REFERENCES part_source(id) ON DELETE CASCADE,
    part       text NOT NULL,
    colour     integer NOT NULL,
    qty        integer NOT NULL CHECK (qty >= 0),
    rb_part    text,
    rb_colour  integer,
    PRIMARY KEY (source_id, part, colour)        -- location_id joins the key in the later "locations" migration
);
CREATE INDEX part_row_part_idx ON part_row (part, colour);

CREATE TABLE part_review (                       -- approximate items (colour families): never counted as exact
    source_id     text NOT NULL REFERENCES part_source(id) ON DELETE CASCADE,
    id            text NOT NULL,
    part          text NOT NULL,
    family        text,
    candidates    integer[] NOT NULL DEFAULT '{}',
    qty           integer NOT NULL CHECK (qty > 0),
    brickit_part  text,
    note          text,
    PRIMARY KEY (source_id, id)
);

CREATE TABLE part_note (                         -- owner decision: a free-text note per part ("blue tub"), later converted to locations
    part       text PRIMARY KEY,
    note       text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE part_change (                       -- <id>.log.jsonl: audit trail and undo stack
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id   text NOT NULL REFERENCES part_source(id) ON DELETE CASCADE,
    batch       uuid NOT NULL,                   -- one user action
    ts          timestamptz NOT NULL DEFAULT now(),
    op          text NOT NULL CHECK (op IN ('add','remove','set','import','replace','resolve','undo')),
    part        text,
    colour      integer,
    delta       integer,
    qty_before  integer,
    qty_after   integer,
    note        text
);
CREATE INDEX part_change_source_batch ON part_change (source_id, batch);
CREATE INDEX part_change_part_idx ON part_change (source_id, part, colour);

-- ---------------------------------------------------------------- cost history and request log
CREATE TABLE usage_event (                       -- append-only; no foreign keys, so deleting a chat keeps the spend
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts            timestamptz NOT NULL DEFAULT now(),
    kind          text NOT NULL CHECK (kind IN ('agent','worker','understand','other')),
    chat_id       text,
    build_id      text,
    message_id    integer,
    worker_id     text,
    model         text,
    prompt        integer NOT NULL DEFAULT 0,
    completion    integer NOT NULL DEFAULT 0,
    cache_read    integer NOT NULL DEFAULT 0,
    cache_write   integer NOT NULL DEFAULT 0,
    cost_usd      numeric(12,6),                 -- NULL = unpriced model (same rule as usage.py)
    eur_per_usd   numeric(8,4)
);
CREATE INDEX usage_event_ts_idx   ON usage_event (ts);
CREATE INDEX usage_event_chat_idx ON usage_event (chat_id) WHERE chat_id IS NOT NULL;

CREATE TABLE request_log (                       -- nl-review section 7; the sentence is stored only when the owner opts in
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts             timestamptz NOT NULL DEFAULT now(),
    surface        text, build_kind text, route text, decided_by text, family text, kind text, generator text,
    text_sha       text, text_len integer, lang text,
    latency_ms     integer,
    applied        boolean, undone_within_60s boolean, went_to_agent boolean,
    usage_event_id bigint REFERENCES usage_event(id) ON DELETE SET NULL,
    sentence       text,                         -- NULL unless opted in
    fields         jsonb NOT NULL DEFAULT '{}'   -- the rest (n_edits, notes_kinds, clarified, chips_used, ...)
);
CREATE INDEX request_log_ts_idx    ON request_log (ts DESC);
CREATE INDEX request_log_route_idx ON request_log (route, ts DESC);

CREATE VIEW cost_daily AS
    SELECT (ts AT TIME ZONE 'UTC')::date AS day, kind,
           count(*) AS calls, sum(cost_usd) AS cost_usd,
           count(*) FILTER (WHERE cost_usd IS NULL) AS unpriced
    FROM usage_event GROUP BY 1, 2;

-- ---------------------------------------------------------------- bookkeeping
CREATE TABLE import_run (                        -- the JSON importer: one row per run, report inside
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    mode        text NOT NULL CHECK (mode IN ('dry-run','commit','verify')),
    ok          boolean,
    report      jsonb NOT NULL DEFAULT '{}'
);
