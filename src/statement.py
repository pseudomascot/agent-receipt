"""Statement page: a local web page listing actions by day or date range.

Local only. Binds to 127.0.0.1 and never to a public interface.
"""

from datetime import date, timedelta
from pathlib import Path

from flask import Flask, Response, abort, redirect, render_template, request

from alerts import mark_seen, unseen, unseen_action_ids, unseen_count
from doctor import status as machine_status
from export import export_csv
from queries import ACTION_TYPES, coverage_notes, day_bounds, earliest_day, list_days, statement, type_label
from store import DB_PATH, connect
from summary import SUMMARIES_DIR, summary_text
from verify import status_line

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

    @app.context_processor
    def _globals():
        conn = db()
        try:
            return {"needs_review": unseen_count(conn), "today": date.today().isoformat(),
                    "type_label": type_label}
        finally:
            conn.close()

    def render_statement(start: date, end: date, base_url: str, nav: str = ""):
        filters = _filters()
        conn = db()
        try:
            data = statement(conn, start, end, filters["type"], filters["agent"], filters["user"])
            integrity = status_line(conn)
            alerted = unseen_action_ids(conn, day_bounds(start)[0], day_bounds(end)[1])
            open_alerts = unseen_count(conn)
        finally:
            conn.close()
        summary_file = summaries_dir / f"{start.isoformat()}.txt"
        return render_template(
            "statement.html", coverage_notes=coverage_notes(),
            summary=summary_text(data, integrity, open_alerts),
            summary_saved=data["single_day"] and summary_file.exists(), base_url=base_url,
            integrity=integrity, alerted=alerted, nav=nav, query=request.query_string.decode(), **data)

    @app.route("/")
    def index():
        conn = db()
        try:
            days = list_days(conn)
            integrity = status_line(conn)
        finally:
            conn.close()
        return render_template("index.html", days=days, coverage_notes=coverage_notes(),
                               integrity=integrity, nav="index", machine=machine_status(db_path))

    @app.route("/day/<day_str>")
    def day_page(day_str):
        day = _parse_day(day_str)
        return render_statement(day, day, f"/day/{day.isoformat()}",
                                nav="today" if day == date.today() else "")

    @app.route("/range/<start_str>/<end_str>")
    def range_page(start_str, end_str):
        start, end = _parse_day(start_str), _parse_day(end_str)
        if end < start:
            start, end = end, start
        return render_statement(start, end, f"/range/{start.isoformat()}/{end.isoformat()}")

    @app.route("/week")
    def week_page():
        end = date.today()
        return render_statement(end - timedelta(days=6), end, "/week", nav="week")

    @app.route("/month")
    def month_page():
        end = date.today()
        return render_statement(end - timedelta(days=29), end, "/month", nav="month")

    @app.route("/all")
    def all_page():
        conn = db()
        try:
            start = earliest_day(conn) or date.today()
        finally:
            conn.close()
        return render_statement(start, date.today(), "/all", nav="all")

    @app.route("/alerts")
    def alerts_page():
        conn = db()
        try:
            items = unseen(conn)
            integrity = status_line(conn)
        finally:
            conn.close()
        return render_template("alerts.html", items=items, coverage_notes=coverage_notes(),
                               integrity=integrity)

    @app.route("/alerts/seen", methods=["POST"])
    def alerts_seen():
        conn = db()
        try:
            if request.form.get("all"):
                mark_seen(conn)
            else:
                ids = [int(i) for i in request.form.getlist("id") if i.isdigit()]
                if ids:
                    mark_seen(conn, ids)
        finally:
            conn.close()
        return redirect("/alerts")

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
