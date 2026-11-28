-- 表结构。观察只追加；核对状态只能由提交产生；配置独立成表并可追溯变更。

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- 观察、核对状态、变化记录共用一个单调序号：跨表可比，用来判断“谁更靠后”。
CREATE TABLE IF NOT EXISTS record_sequence (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    value INTEGER NOT NULL
);

INSERT OR IGNORE INTO record_sequence(id, value) VALUES (1, 0);

-- 一次整柜观察。作废的同样落库，只是不作为当前状态。
CREATE TABLE IF NOT EXISTS observation (
    id          INTEGER PRIMARY KEY,
    status      TEXT    NOT NULL,              -- done | void
    void_reason TEXT    NOT NULL DEFAULT '',
    trigger     TEXT    NOT NULL DEFAULT '',   -- door | timer | manual
    started_at  TEXT    NOT NULL,
    finished_at TEXT    NOT NULL DEFAULT ''
);

-- 某一层的结果。usable=0 时整次观察作废，该层没有实体。
CREATE TABLE IF NOT EXISTS layer_record (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id INTEGER NOT NULL REFERENCES observation(id) ON DELETE CASCADE,
    layer_id       TEXT    NOT NULL,
    layer_name     TEXT    NOT NULL DEFAULT '',
    usable         INTEGER NOT NULL DEFAULT 1,
    reason         TEXT    NOT NULL DEFAULT '',
    image_path     TEXT    NOT NULL DEFAULT '',
    UNIQUE (observation_id, layer_id)
);

-- 层内的一件。已落库不可改；人工修改发生在编辑草稿里，提交后成为新的核对状态。
CREATE TABLE IF NOT EXISTS entity (
    id            TEXT    PRIMARY KEY,
    layer_record_id INTEGER NOT NULL REFERENCES layer_record(id) ON DELETE CASCADE,
    x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
    category_id   INTEGER,                     -- NULL = 未识别
    crop_path     TEXT    NOT NULL DEFAULT '',
    score         REAL,                        -- 检测原始分，仅供排查
    match_score   REAL                         -- 分类相似度，仅供排查
);

CREATE INDEX IF NOT EXISTS idx_entity_layer ON entity(layer_record_id);

CREATE TABLE IF NOT EXISTS category (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    shape      TEXT    NOT NULL DEFAULT '',
    details    TEXT    NOT NULL DEFAULT '{}',  -- 详情表单 JSON
    created_at TEXT    NOT NULL
);

-- 每类多张原型；检索时比「最相似的那一张」，不是比类均值。
CREATE TABLE IF NOT EXISTS reference (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER NOT NULL REFERENCES category(id) ON DELETE CASCADE,
    path        TEXT    NOT NULL,
    feature     BLOB    NOT NULL,
    dim         INTEGER NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reference_category ON reference(category_id);

-- 类别详情表单的字段定义。category_id 为空 = 全局模板，否则只属于那个类别。
-- 两级同 key 时类别自己的覆盖全局的，所以这里允许同一个 key 出现两次（不同级）。
CREATE TABLE IF NOT EXISTS field_def (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id INTEGER REFERENCES category(id) ON DELETE CASCADE,
    key         TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    askable     INTEGER NOT NULL DEFAULT 1,
    position    INTEGER NOT NULL DEFAULT 0
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_field_scope_key
    ON field_def(IFNULL(category_id, 0), key);

-- 合并过的来源类别：来源行已硬删，但历史里引用它的地方要随访到目标类别的名字。
CREATE TABLE IF NOT EXISTS category_merge (
    source_id INTEGER PRIMARY KEY,
    target_id INTEGER NOT NULL
);

-- 人提交的整柜事实，每次提交新增一行，旧版本保留。
CREATE TABLE IF NOT EXISTS checkpoint (
    id                   INTEGER PRIMARY KEY,
    based_on_observation INTEGER,
    reported             TEXT NOT NULL,        -- 系统当时报的
    submitted            TEXT NOT NULL,        -- 人提交的
    note                 TEXT NOT NULL DEFAULT '',
    created_at           TEXT NOT NULL
);

-- 相对上一次观察确有净变化时记一条；内容相对最新核对状态。
CREATE TABLE IF NOT EXISTS change_record (
    id             INTEGER PRIMARY KEY,
    observation_id INTEGER NOT NULL REFERENCES observation(id) ON DELETE CASCADE,
    items          TEXT NOT NULL,              -- JSON
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS setting (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,                  -- JSON
    updated_at TEXT NOT NULL
);

-- 敏感项只记「改过」，不记新旧明文。
CREATE TABLE IF NOT EXISTS setting_change (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT    NOT NULL,
    is_sensitive INTEGER NOT NULL DEFAULT 0,
    old_value   TEXT,
    new_value   TEXT,
    changed_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_setting_change_key ON setting_change(key);
