"""SQLite 存取：历版页稿与每版引文核对结果。"""

import os
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    version INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS citations (
    version INTEGER NOT NULL,
    citation_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('located', 'pending')),
    start_pos INTEGER NOT NULL,
    end_pos INTEGER NOT NULL,
    located_version INTEGER NOT NULL,
    PRIMARY KEY (version, citation_id)
);
"""


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(current_app.config["DATABASE_PATH"])
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
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
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
