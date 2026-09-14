"""One SQLite database for everything. Schema lives here."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "agent_receipt.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS input_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('key', 'click'))
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    agent TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('log', 'input', 'email', 'card', 'wallet')),
    action_type TEXT NOT NULL CHECK (action_type IN
        ('send_email', 'create_event', 'purchase', 'file_write', 'post', 'execute', 'other')),
    target TEXT,
    amount REAL,
    currency TEXT,
    artifact_link TEXT,
    reversible INTEGER,
    attribution TEXT NOT NULL CHECK (attribution IN ('agent', 'human', 'unknown')),
    confidence_note TEXT,
    raw_json TEXT,
    source_ref TEXT UNIQUE
);

-- When each collector was actually running. Gaps here are gaps in the receipt,
-- and the statement must say so rather than imply "nothing happened".
CREATE TABLE IF NOT EXISTS coverage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL CHECK (source IN ('log', 'input', 'email', 'card', 'wallet')),
    started_at REAL NOT NULL,
    ended_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_actions_timestamp ON actions (timestamp);
CREATE INDEX IF NOT EXISTS idx_coverage_range ON coverage (source, started_at, ended_at);
CREATE INDEX IF NOT EXISTS idx_input_events_timestamp ON input_events (timestamp);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    # check_same_thread=False because the input monitor writes from pynput's
    # listener threads. Callers that share a connection across threads must
    # serialize writes themselves.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.executescript(SCHEMA)
    return conn
