"""Run everything: input monitor + statement page + periodic refresh.

Started by "Agent Receipt.command". Closing that window (or Ctrl+C) stops all
three. Nothing here talks to the network except the local page on 127.0.0.1.
"""

import signal
import subprocess
import sys
import time
import webbrowser
from datetime import date
from pathlib import Path

from correlator import correlate
from log_parser import find_transcripts, ingest, prune
from statement import HOST, PORT
from store import DB_PATH, connect
from summary import SUMMARIES_DIR, write_summary

SRC = Path(__file__).resolve().parent
REFRESH_SECONDS = 300


def refresh(db_path=DB_PATH, transcripts_root=None, summaries_dir=SUMMARIES_DIR) -> dict:
    """Parse new log entries, re-attribute, and rewrite today's summary."""
    conn = connect(db_path)
    try:
        paths = find_transcripts(transcripts_root) if transcripts_root else find_transcripts()
        pruned = prune(conn)
        new = ingest(conn, paths)
        counts = correlate(conn)
        write_summary(conn, date.today(), summaries_dir)
    finally:
        conn.close()
    return {"new": new, "pruned": pruned, **counts}


def _child(script: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, str(SRC / script)])


def _stop(signum, frame):
    raise KeyboardInterrupt


def main() -> None:
    # Closing the Terminal window sends SIGHUP; kill sends SIGTERM. Route both
    # through the same shutdown path so the children never outlive us.
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)
    print("Agent Receipt")
    print(f"  database:  {DB_PATH}")
    print(f"  statement: http://{HOST}:{PORT}/")
    print("  Close this window or press Ctrl+C to stop.\n")

    monitor = _child("input_monitor.py")
    page = _child("statement.py")
    time.sleep(1.5)
    webbrowser.open(f"http://{HOST}:{PORT}/")

    try:
        while True:
            if monitor.poll() is not None:
                print("Input monitor stopped unexpectedly. Check Accessibility permission for Terminal.")
            if page.poll() is not None:
                print("Statement page stopped unexpectedly.")
            result = refresh()
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] refreshed: {result['new']} new action(s); "
                  f"agent {result['agent']}, human {result['human']}, unknown {result['unknown']}")
            time.sleep(REFRESH_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        for proc in (monitor, page):
            if proc.poll() is None:
                proc.terminate()
        for proc in (monitor, page):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
