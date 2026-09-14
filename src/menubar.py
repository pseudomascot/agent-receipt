"""Menu-bar app: the runner without a Terminal window.

Started by "Agent Receipt.app". Runs the input monitor and statement page as
child processes, refreshes every five minutes in a background thread, and
shows a small menu: Open statement, Needs review (N), Status, Quit.
"""

import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))

from run import REFRESH_SECONDS, VERIFY_SECONDS, _child, refresh  # noqa: E402
from statement import HOST, PORT  # noqa: E402

URL = f"http://{HOST}:{PORT}/"


def menu_title(needs_review: int) -> str:
    return "🧾" if not needs_review else f"🧾 {needs_review}"


class Runner:
    """Children + background refresh loop, independent of any UI toolkit."""

    def __init__(self):
        self.monitor = _child("input_monitor.py")
        self.page = _child("statement.py")
        self.last = {}
        self.last_error = None
        self.needs_review = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        from store import connect
        from verify import verify
        from alerts import unseen_count
        last_verify = 0.0
        while not self._stop.is_set():
            try:
                self.last = refresh()
                conn = connect()
                try:
                    if time.time() - last_verify > VERIFY_SECONDS:
                        verify(conn)
                        last_verify = time.time()
                    self.needs_review = unseen_count(conn)
                finally:
                    conn.close()
                self.last_error = None
            except Exception as exc:  # keep the app alive; show it in Status
                self.last_error = repr(exc)
            self._stop.wait(REFRESH_SECONDS)

    def children_alive(self) -> dict:
        return {"input monitor": self.monitor.poll() is None, "statement page": self.page.poll() is None}

    def stop(self):
        self._stop.set()
        for proc in (self.monitor, self.page):
            if proc.poll() is None:
                proc.terminate()
        for proc in (self.monitor, self.page):
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def main():
    import rumps
    from doctor import report, status

    runner = Runner()
    time.sleep(1.5)
    webbrowser.open(URL)

    class App(rumps.App):
        def __init__(self):
            super().__init__("Agent Receipt", title=menu_title(0), quit_button=None)
            self.review_item = rumps.MenuItem("Needs review", callback=self.open_review)
            self.menu = [rumps.MenuItem("Open statement", callback=self.open_statement), self.review_item, None,
                         rumps.MenuItem("Status…", callback=self.show_status), None,
                         rumps.MenuItem("Quit Agent Receipt", callback=self.quit)]
            self.badge_timer = rumps.Timer(self.update_badge, 10)
            self.badge_timer.start()

        def update_badge(self, _=None):
            n = runner.needs_review
            self.title = menu_title(n)
            self.review_item.title = f"Needs review ({n})" if n else "Needs review"

        def open_statement(self, _):
            webbrowser.open(URL)

        def open_review(self, _):
            webbrowser.open(URL + "alerts")

        def show_status(self, _):
            alive = runner.children_alive()
            extra = "\n".join(f"{'✓' if ok else '✗'} {name}" for name, ok in alive.items())
            if runner.last_error:
                extra += f"\n\nLast refresh problem: {runner.last_error}"
            rumps.alert("Agent Receipt — status", report(status()) + "\n\n" + extra)

        def quit(self, _):
            runner.stop()
            rumps.quit_application()

    App().run()


if __name__ == "__main__":
    main()
