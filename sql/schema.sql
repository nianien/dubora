-- Pipeline DB schema v4
-- 10 tables: dramas, episodes, tasks, events, artifacts, utterances, utterance_cues, cues, roles, dictionary

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS dramas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE,
    synopsis    TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drama_id    INTEGER NOT NULL REFERENCES dramas(id),
    name        TEXT NOT NULL,
    path        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'ready',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(drama_id, name)
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drama_id    INTEGER NOT NULL,
    episode_id  INTEGER NOT NULL REFERENCES episodes(id),
    type        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    context     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    claimed_at  TEXT,
    finished_at TEXT,
    error       TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     INTEGER NOT NULL REFERENCES tasks(id),
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    data        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);

CREATE TABLE IF NOT EXISTS artifacts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id  INTEGER NOT NULL REFERENCES episodes(id),
    key         TEXT NOT NULL,
    relpath     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    UNIQUE(episode_id, key)
);

-- utterances: virtual grouping + TTS cache
-- text_cn/text_en are redundant caches merged from sub-cues (not hand-edited)
CREATE TABLE IF NOT EXISTS utterances (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id      INTEGER NOT NULL REFERENCES episodes(id),
    text_cn         TEXT NOT NULL DEFAULT '',
    text_en         TEXT NOT NULL DEFAULT '',
    speaker         TEXT NOT NULL DEFAULT '',
    emotion         TEXT NOT NULL DEFAULT 'neutral',
    gender          TEXT,
    kind            TEXT NOT NULL DEFAULT 'speech',
    tts_policy      TEXT,
    source_hash     TEXT,
    voice_hash      TEXT,
    audio_path      TEXT,
    tts_duration_ms INTEGER,
    tts_rate        REAL,
    tts_error       TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_utterances_episode ON utterances(episode_id);

-- utterance_cues: links utterances to their constituent SRC cues
CREATE TABLE IF NOT EXISTS utterance_cues (
    utterance_id INTEGER NOT NULL REFERENCES utterances(id),
    cue_id       INTEGER NOT NULL REFERENCES cues(id),
    PRIMARY KEY (utterance_id, cue_id)
);

-- cues: atomic segments, independent of utterances
CREATE TABLE IF NOT EXISTS cues (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    episode_id   INTEGER NOT NULL REFERENCES episodes(id),
    text         TEXT NOT NULL DEFAULT '',
    text_en      TEXT NOT NULL DEFAULT '',
    start_ms     INTEGER NOT NULL,
    end_ms       INTEGER NOT NULL,
    speaker      TEXT NOT NULL DEFAULT '',
    emotion      TEXT NOT NULL DEFAULT 'neutral',
    gender       TEXT,
    kind         TEXT NOT NULL DEFAULT 'speech',
    cv           INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cues_episode ON cues(episode_id);

CREATE TABLE IF NOT EXISTS roles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drama_id    INTEGER NOT NULL REFERENCES dramas(id),
    name        TEXT NOT NULL,
    voice_type  TEXT NOT NULL DEFAULT '',
    role_type   TEXT NOT NULL DEFAULT 'extra',
    UNIQUE(drama_id, name)
);

CREATE TABLE IF NOT EXISTS dictionary (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    drama_id    INTEGER NOT NULL REFERENCES dramas(id),
    type        TEXT NOT NULL,
    src         TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    UNIQUE(drama_id, type, src)
);
