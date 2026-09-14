"""Input monitor: records that a key or click happened, and when.

Non-negotiable, per CLAUDE.md:
- Never which key. Never mouse position. Never screenshots. Never window titles.
- Two columns of fact per event: timestamp, kind ("key" or "click"). Nothing else.
"""

import sqlite3
import threading
import time
from pathlib import Path

from store import DB_PATH, connect


def init_db(db_path: Path = DB_PATH) -> sqlite3.Connection:
    return connect(db_path)


def log_event(conn: sqlite3.Connection, kind: str) -> None:
    if kind not in ("key", "click"):
        raise ValueError(f"kind must be 'key' or 'click', got {kind!r}")
    conn.execute(
        "INSERT INTO input_events (timestamp, kind) VALUES (?, ?)",
        (time.time(), kind),
    )
    conn.commit()


HEARTBEAT_SECONDS = 5


def start_coverage(conn: sqlite3.Connection) -> int:
    now = time.time()
    cur = conn.execute(
        "INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)",
        (now, now),
    )
    conn.commit()
    return cur.lastrowid


def heartbeat(conn: sqlite3.Connection, coverage_id: int) -> None:
    conn.execute("UPDATE coverage SET ended_at = ? WHERE id = ?", (time.time(), coverage_id))
    conn.commit()


def run(db_path: Path = DB_PATH) -> None:
    from pynput import keyboard, mouse

    conn = init_db(db_path)
    write_lock = threading.Lock()
    coverage_id = start_coverage(conn)
    print(f"Logging key/click timestamps to {db_path}")
    print("No key values, no positions, no content are recorded. Press Ctrl+C to stop.")

    def on_press(_key):
        with write_lock:
            log_event(conn, "key")

    def on_click(_x, _y, _button, pressed):
        if pressed:
            with write_lock:
                log_event(conn, "click")

    key_listener = keyboard.Listener(on_press=on_press)
    mouse_listener = mouse.Listener(on_click=on_click)
    key_listener.start()
    mouse_listener.start()

    try:
        while True:
            time.sleep(HEARTBEAT_SECONDS)
            with write_lock:
                heartbeat(conn, coverage_id)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        key_listener.stop()
        mouse_listener.stop()
        with write_lock:
            heartbeat(conn, coverage_id)
        conn.close()


if __name__ == "__main__":
    run()
