"""Statement page: a local web page listing actions by day or date range.

Local only. Binds to 127.0.0.1 and never to a public interface.
"""

from datetime import date, timedelta
from pathlib import Path

from flask import Flask, Response, abort, render_template, request

from export import export_csv
from queries import ACTION_TYPES, COVERAGE_NOTES, earliest_day, list_days, statement
from store import DB_PATH, connect
from summary import SUMMARIES_DIR, summary_text

HOST = "127.0.0.1"
PORT = 8765


def _filters() -> dict:
    type_filter = request.args.get("type")
    return {
        "type": type_filter if type_filter in ACTION_TYPES else None,
        "agent": request.args.get("agent") or None,
        "user": request.args.get("user") or None,
    }


def _parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        abort(404)


def create_app(db_path: Path = DB_PATH, summaries_dir: Path = SUMMARIES_DIR) -> Flask:
    app = Flask(__name__, template_folder=str(Path(__file__).resolve().parent / "templates"))

    def db():
        return connect(db_path)

    def render_statement(start: date, end: date, base_url: str):
        filters = _filters()
        conn = db()
        try:
            data = statement(conn, start, end, filters["type"], filters["agent"], filters["user"])
        finally:
            conn.close()
        summary_file = summaries_dir / f"{start.isoformat()}.txt"
        return render_template(
            "statement.html", coverage_notes=COVERAGE_NOTES, summary=summary_text(data),
            summary_saved=data["single_day"] and summary_file.exists(), base_url=base_url,
            query=request.query_string.decode(), **data)

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
        day = _parse_day(day_str)
        return render_statement(day, day, f"/day/{day.isoformat()}")

    @app.route("/range/<start_str>/<end_str>")
    def range_page(start_str, end_str):
        start, end = _parse_day(start_str), _parse_day(end_str)
        if end < start:
            start, end = end, start
        return render_statement(start, end, f"/range/{start.isoformat()}/{end.isoformat()}")

    @app.route("/week")
    def week_page():
        end = date.today()
        return render_statement(end - timedelta(days=6), end, "/week")

    @app.route("/month")
    def month_page():
        end = date.today()
        return render_statement(end - timedelta(days=29), end, "/month")

    @app.route("/all")
    def all_page():
        conn = db()
        try:
            start = earliest_day(conn) or date.today()
        finally:
            conn.close()
        return render_statement(start, date.today(), "/all")

    @app.route("/export.csv")
    def export():
        start = _parse_day(request.args.get("start", ""))
        end = _parse_day(request.args.get("end", ""))
        filters = _filters()
        conn = db()
        try:
            data = statement(conn, start, end, filters["type"], filters["agent"], filters["user"])
            text = export_csv(conn, data, filters)
        finally:
            conn.close()
        name = f"agent-receipt_{data['start']}" + ("" if data["single_day"] else f"_to_{data['end']}") + ".csv"
        return Response(text, mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return app


if __name__ == "__main__":
    print(f"Agent Receipt statement: http://{HOST}:{PORT}/  (local only; Ctrl+C to stop)")
    create_app().run(host=HOST, port=PORT, debug=False)
