"""Input monitor: records that a key or click happened, and when.

Non-negotiable, per CLAUDE.md:
- Never which key. Never mouse position. Never screenshots. Never window titles.
- Two columns of fact per event: timestamp, kind ("key" or "click"). Nothing else.
"""

import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "agent_receipt.db"


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS input_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            kind TEXT NOT NULL CHECK (kind IN ('key', 'click'))
        )
        """
    )
    conn.commit()
    return conn


def log_event(conn: sqlite3.Connection, kind: str) -> None:
    if kind not in ("key", "click"):
        raise ValueError(f"kind must be 'key' or 'click', got {kind!r}")
    conn.execute(
        "INSERT INTO input_events (timestamp, kind) VALUES (?, ?)",
        (time.time(), kind),
    )
    conn.commit()


def run(db_path: Path = DB_PATH) -> None:
    from pynput import keyboard, mouse

    conn = init_db(db_path)
    print(f"Logging key/click timestamps to {db_path}")
    print("No key values, no positions, no content are recorded. Press Ctrl+C to stop.")

    def on_press(_key):
        log_event(conn, "key")

    def on_click(_x, _y, _button, pressed):
        if pressed:
            log_event(conn, "click")

    key_listener = keyboard.Listener(on_press=on_press)
    mouse_listener = mouse.Listener(on_click=on_click)
    key_listener.start()
    mouse_listener.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        key_listener.stop()
        mouse_listener.stop()
        conn.close()


if __name__ == "__main__":
    run()
