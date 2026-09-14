"""Statement page: a local web page listing actions by day.

Local only. Binds to 127.0.0.1 and never to a public interface.
"""

from datetime import date
from pathlib import Path

from flask import Flask, abort, render_template

from queries import COVERAGE_NOTES, day_statement, list_days
from store import DB_PATH, connect
from summary import SUMMARIES_DIR, build_summary

HOST = "127.0.0.1"
PORT = 8765


def create_app(db_path: Path = DB_PATH, summaries_dir: Path = SUMMARIES_DIR) -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parent / "templates"))

    def db():
        return connect(db_path)

    @app.route("/")
    def index():
        conn = db()
        try:
            days = list_days(conn)
        finally:
            conn.close()
        return render_template("index.html", days=days, coverage_notes=COVERAGE_NOTES,
                               today=date.today().isoformat())

    @app.route("/day/<day_str>")
    def day_page(day_str):
        try:
            day = date.fromisoformat(day_str)
        except ValueError:
            abort(404)
        conn = db()
        try:
            data = day_statement(conn, day)
            summary = build_summary(conn, day)
        finally:
            conn.close()
        summary_file = summaries_dir / f"{day.isoformat()}.txt"
        return render_template("day.html", coverage_notes=COVERAGE_NOTES, summary=summary,
                               summary_saved=summary_file.exists(), **data)

    return app


if __name__ == "__main__":
    print(f"Agent Receipt statement: http://{HOST}:{PORT}/  (local only; Ctrl+C to stop)")
    create_app().run(host=HOST, port=PORT, debug=False)
