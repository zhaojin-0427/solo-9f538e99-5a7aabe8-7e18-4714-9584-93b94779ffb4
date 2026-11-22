"""SQLite 存取：历版页稿、每版已定位引文、引文目录。"""

import os
import sqlite3
import time

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    version INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
-- 本版已定位的引文（区间用于“本版已定位引文完整覆盖命中区间则跳过”）。
CREATE TABLE IF NOT EXISTS citations (
    version INTEGER NOT NULL,
    citation_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('located', 'pending')),
    start_pos INTEGER NOT NULL,
    end_pos INTEGER NOT NULL,
    located_version INTEGER NOT NULL,
    PRIMARY KEY (version, citation_id)
);

-- 引文目录按“目录版号”整份保存。替换目录只追加新版本，旧版本继续保留，
-- 正在核查的窗口仍可读取自己钉住的那一版。
CREATE TABLE IF NOT EXISTS catalog_versions (
    version INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    entry_count INTEGER NOT NULL,
    source_bytes INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS catalog_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_version INTEGER NOT NULL,
    book_no TEXT NOT NULL,
    citation TEXT NOT NULL,
    norm TEXT NOT NULL,
    norm_len INTEGER NOT NULL,
    gram_count INTEGER NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_catalog_entries_norm
    ON catalog_entries(catalog_version, norm);

-- 每条引文只索引首、中、末三个四字组合。引文若在原稿中出现，这三个组合
-- 必然都出现，因此可用它们从索引取出候选，无需逐条扫描整份目录。
CREATE TABLE IF NOT EXISTS catalog_grams (
    catalog_version INTEGER NOT NULL,
    gram TEXT NOT NULL,
    entry_id INTEGER NOT NULL,
    PRIMARY KEY (catalog_version, gram, entry_id)
) WITHOUT ROWID;
"""


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE_PATH"], timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        g.db = conn
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db(path):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    # 多个 gunicorn worker 同时启动时可能争用建表/切换 WAL，稍等重试即可。
    last_error = None
    for _attempt in range(10):
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout = 30000")
            # WAL：替换目录的写入事务进行时，其他窗口仍能读取旧目录。
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(SCHEMA)
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            time.sleep(0.2)
        finally:
            conn.close()
    raise last_error
