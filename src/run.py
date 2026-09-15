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

from alerts import evaluate, notify, notify_new
from correlator import correlate
from calendar_connector import sync_if_configured as calendar_sync_if_configured
from email_connector import sync_if_configured
from log_parser import SOURCES, current_user, ingest_all, reconcile
from stripe_connector import sync_if_configured as stripe_sync_if_configured
from statement import HOST, PORT
from store import DB_PATH, connect
from summary import SUMMARIES_DIR, write_summary
from verify import describe, verify

SRC = Path(__file__).resolve().parent
REFRESH_SECONDS = 300
VERIFY_SECONDS = 6 * 3600


def refresh(db_path=DB_PATH, sources=SOURCES, summaries_dir=SUMMARIES_DIR, notifier=notify,
            email: bool = True) -> dict:
    """Parse new log entries, re-attribute, raise alerts, and rewrite today's summary."""
    conn = connect(db_path)
    try:
        removed, updated = reconcile(conn)
        new = ingest_all(conn, sources)
        mail = sync_if_configured(conn, current_user()) if email else None
        stripe = stripe_sync_if_configured(conn, current_user()) if email else None
        calendar = calendar_sync_if_configured(conn, current_user()) if email else None
        counts = correlate(conn)
        new_alerts = evaluate(conn)
        notified = notify_new(new_alerts, notifier)
        write_summary(conn, date.today(), summaries_dir)
    finally:
        conn.close()
    result = {"new": new, "removed": removed, "updated": updated, "alerts": len(new_alerts),
              "notified": notified, **counts}
    if mail:
        result["mail"] = mail
    if stripe:
        result["stripe"] = stripe
    if calendar:
        result["calendar"] = calendar
    return result


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

    last_verify = 0.0
    try:
        while True:
            if monitor.poll() is not None:
                print("Input monitor stopped unexpectedly. Check Accessibility permission for Terminal.")
            if page.poll() is not None:
                print("Statement page stopped unexpectedly.")
            result = refresh()
            stamp = time.strftime("%H:%M:%S")
            print(f"[{stamp}] refreshed: {result['new']} new action(s), {result['alerts']} new alert(s); "
                  f"agent {result['agent']}, human {result['human']}, unknown {result['unknown']}")
            if time.time() - last_verify > VERIFY_SECONDS:
                conn = connect()
                try:
                    print(f"[{stamp}] {describe(verify(conn))}")
                finally:
                    conn.close()
                last_verify = time.time()
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
