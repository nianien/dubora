-- Pipeline DB schema v6 (PostgreSQL)
-- 12 tables: users, user_auths, dramas, episodes, tasks, events,
--            utterances, utterance_cues, cues, roles, glossary, artifacts

CREATE TABLE IF NOT EXISTS users (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    email       TEXT NOT NULL UNIQUE,
    picture     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_auths (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id),
    provider    TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    email       TEXT NOT NULL DEFAULT '',
    raw         TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(provider, provider_id)
);

CREATE TABLE IF NOT EXISTS dramas (
    id               SERIAL PRIMARY KEY,
    name             TEXT NOT NULL,
    user_id          INTEGER NOT NULL REFERENCES users(id),
    synopsis         TEXT NOT NULL DEFAULT '',
    cover_image      TEXT NOT NULL DEFAULT '',
    total_episodes   INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL,
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS episodes (
    id          SERIAL PRIMARY KEY,
    drama_id    INTEGER NOT NULL REFERENCES dramas(id),
    number      INTEGER NOT NULL,
    path        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'ready',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE(drama_id, number)
);

CREATE TABLE IF NOT EXISTS tasks (
    id          SERIAL PRIMARY KEY,
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
    id          SERIAL PRIMARY KEY,
    task_id     INTEGER NOT NULL REFERENCES tasks(id),
    ts          TEXT NOT NULL,
    kind        TEXT NOT NULL,
    data        TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_task ON events(task_id);

CREATE TABLE IF NOT EXISTS utterances (
    id              SERIAL PRIMARY KEY,
    episode_id      INTEGER NOT NULL REFERENCES episodes(id),
    text_cn         TEXT NOT NULL DEFAULT '',
    text_en         TEXT NOT NULL DEFAULT '',
    start_ms        INTEGER NOT NULL DEFAULT 0,
    end_ms          INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE IF NOT EXISTS cues (
    id           SERIAL PRIMARY KEY,
    episode_id   INTEGER NOT NULL REFERENCES episodes(id),
    text         TEXT NOT NULL DEFAULT '',
    text_en      TEXT NOT NULL DEFAULT '',
    start_ms     INTEGER NOT NULL,
    end_ms       INTEGER NOT NULL,
    speaker      TEXT NOT NULL DEFAULT '',
    emotion      TEXT NOT NULL DEFAULT 'neutral',
    gender       TEXT,
    kind         TEXT NOT NULL DEFAULT 'speech',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cues_episode ON cues(episode_id);

CREATE TABLE IF NOT EXISTS utterance_cues (
    utterance_id INTEGER NOT NULL REFERENCES utterances(id),
    cue_id       INTEGER NOT NULL REFERENCES cues(id),
    PRIMARY KEY (utterance_id, cue_id)
);

CREATE TABLE IF NOT EXISTS roles (
    id            SERIAL PRIMARY KEY,
    drama_id      INTEGER NOT NULL REFERENCES dramas(id),
    name          TEXT NOT NULL,
    voice_type    TEXT NOT NULL DEFAULT '',
    role_type     TEXT NOT NULL DEFAULT 'extra',
    sample_audio  TEXT NOT NULL DEFAULT '',
    UNIQUE(drama_id, name)
);

CREATE TABLE IF NOT EXISTS glossary (
    id          SERIAL PRIMARY KEY,
    drama_id    INTEGER NOT NULL REFERENCES dramas(id),
    type        TEXT NOT NULL,
    src         TEXT NOT NULL,
    target      TEXT NOT NULL DEFAULT '',
    UNIQUE(drama_id, type, src)
);

CREATE TABLE IF NOT EXISTS artifacts (
    id          SERIAL PRIMARY KEY,
    episode_id  INTEGER NOT NULL REFERENCES episodes(id),
    kind        TEXT NOT NULL,
    gcs_path    TEXT,
    checksum    TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE(episode_id, kind)
);
