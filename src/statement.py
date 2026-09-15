"""Statement page: a local web page listing actions by day or date range.

Local only. Binds to 127.0.0.1 and never to a public interface.
"""

from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode

from flask import Flask, Response, abort, redirect, render_template, request

from agents import display as agent_display_fn
from agents import history as agent_history, known_identities, list_agents, nicknames, restore, retire, set_nickname
from agents import retired as retired_agents
from alerts import RULES, mark_seen, save_settings, unseen, unseen_action_ids, unseen_count
from alerts import settings as alert_settings
from doctor import status as machine_status
from export import export_csv, money_csv
from config import ramp_settings
from log_parser import current_user
from ramp_connector import RampError, issue_fund
from queries import (ACTION_TYPES, coverage_notes, day_bounds, earliest_day, list_days, money_summary,
                     statement, type_label)
from stop import controls as stop_controls
from stop import presses as stop_presses
from stop import run as stop_run_control
from store import DB_PATH, connect
from summary import SUMMARIES_DIR, agent_names, summary_text
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
            nicks, known = nicknames(conn), known_identities()
            return {"needs_review": unseen_count(conn), "today": date.today().isoformat(),
                    "type_label": type_label,
                    "agent_display": lambda name: agent_display_fn(name, nicks, known)}
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
            retired = retired_agents(conn)
            names = agent_names(conn)
        finally:
            conn.close()
        summary_file = summaries_dir / f"{start.isoformat()}.txt"
        return render_template(
            "statement.html", coverage_notes=coverage_notes(),
            summary=summary_text(data, integrity, open_alerts, names),
            summary_saved=data["single_day"] and summary_file.exists(), base_url=base_url,
            integrity=integrity, alerted=alerted, nav=nav, query=request.query_string.decode(),
            retired=retired, **data)

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

    def render_money(start: date | None, end: date):
        filters = _filters()
        conn = db()
        try:
            if start is None:
                start = earliest_day(conn) or end
            data = statement(conn, start, end, filters["type"], filters["agent"], filters["user"])
            integrity = status_line(conn)
        finally:
            conn.close()
        money = money_summary(data)
        return render_template("money.html", money=money, coverage_notes=coverage_notes(),
                               integrity=integrity, nav="money", start=data["start"], end=data["end"],
                               label=data["label"], agent_filter=filters["agent"], user_filter=filters["user"])

    @app.route("/money")
    def money_page():
        return render_money(None, date.today())

    @app.route("/money/<start_str>/<end_str>")
    def money_range_page(start_str, end_str):
        start, end = _parse_day(start_str), _parse_day(end_str)
        if end < start:
            start, end = end, start
        return render_money(start, end)

    @app.route("/export-money.csv")
    def export_money():
        start = _parse_day(request.args.get("start", ""))
        end = _parse_day(request.args.get("end", ""))
        filters = _filters()
        conn = db()
        try:
            data = statement(conn, start, end, filters["type"], filters["agent"], filters["user"])
        finally:
            conn.close()
        money = money_summary(data)
        currency = (request.args.get("currency") or (money["currencies"][0] if money["currencies"] else "USD")).upper()
        text = money_csv(money, currency)
        name = f"agent-receipt_money_{currency}_{data['start']}_to_{data['end']}.csv"
        return Response(text, mimetype="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.route("/agents")
    def agents_page():
        conn = db()
        try:
            items = list_agents(conn)
            changes = agent_history(conn)
            integrity = status_line(conn)
        finally:
            conn.close()
        ramp = ramp_settings()
        return render_template("agents.html", agents=items, changes=changes, coverage_notes=coverage_notes(),
                               integrity=integrity, nav="agents", ramp_mode=ramp["mode"] if ramp else None,
                               ramp_ready=bool(ramp and ramp.get("user_id")), message=request.args.get("msg"),
                               ok=request.args.get("ok") == "1")

    @app.route("/agents/card", methods=["POST"])
    def agents_card():
        agent = (request.form.get("agent") or "").strip()
        interval = (request.form.get("interval") or "MONTHLY").strip().upper()
        try:
            limit = float(request.form.get("limit") or 0)
        except ValueError:
            limit = 0.0
        settings = ramp_settings()
        if not settings or not agent:
            return redirect("/agents?" + urlencode({"msg": "Ramp is not configured (docs/RAMP.md)", "ok": "0"}))
        conn = db()
        try:
            label = agent_display_fn(agent, nicknames(conn), known_identities())["name"]
            try:
                fund = issue_fund(conn, settings, agent, label, limit, interval, current_user())
                msg, ok = (f"Gave {label} a Ramp card: {limit:,.2f} USD per {interval.lower()}"
                           + (f", card •••• {fund['last4']}" if fund["last4"] else "") + f" ({settings['mode']})"), "1"
            except (RampError, OSError, ValueError, KeyError) as exc:
                msg, ok = f"Ramp did not issue the card: {exc}", "0"
        finally:
            conn.close()
        return redirect("/agents?" + urlencode({"msg": msg, "ok": ok}))

    @app.route("/agents/status", methods=["POST"])
    def agents_status():
        agent = (request.form.get("agent") or "").strip()
        action = request.form.get("action")
        note = (request.form.get("note") or "").strip()[:200]
        if agent and action in ("retire", "restore"):
            conn = db()
            try:
                (retire if action == "retire" else restore)(conn, agent, current_user(), note)
            finally:
                conn.close()
        return redirect("/agents")

    @app.route("/agents/name", methods=["POST"])
    def agents_name():
        agent = (request.form.get("agent") or "").strip()
        conn = db()
        try:
            set_nickname(conn, agent, request.form.get("nickname") or "", current_user())
        finally:
            conn.close()
        return redirect("/agents")

    @app.route("/stop")
    def stop_page():
        conn = db()
        try:
            history = stop_presses(conn)
            integrity = status_line(conn)
        finally:
            conn.close()
        conn = db()
        try:
            items = stop_controls(None, conn)
        finally:
            conn.close()
        groups = {}
        for c in items:
            groups.setdefault(c["agent"], []).append(c)
        return render_template("stop.html", groups=list(groups.items()), history=history,
                               message=request.args.get("msg"), ok=request.args.get("ok") == "1",
                               coverage_notes=coverage_notes(), integrity=integrity, nav="stop")

    @app.route("/stop/run", methods=["POST"])
    def stop_run():
        control_id = (request.form.get("id") or "").strip()
        conn = db()
        try:
            result = stop_run_control(conn, control_id, current_user())
        finally:
            conn.close()
        return redirect("/stop?" + urlencode({"msg": result["message"], "ok": "1" if result["ok"] else "0"}))

    @app.route("/alerts")
    def alerts_page():
        conn = db()
        try:
            items = unseen(conn)
            integrity = status_line(conn)
            rules = alert_settings(conn)
        finally:
            conn.close()
        return render_template("alerts.html", items=items, coverage_notes=coverage_notes(),
                               integrity=integrity, rules=rules, rule_names=RULES,
                               saved=request.args.get("saved") == "1")

    @app.route("/alerts/rules", methods=["POST"])
    def alerts_rules():
        conn = db()
        try:
            save_settings(conn, {k: request.form.getlist(k) for k in request.form}, current_user())
        finally:
            conn.close()
        return redirect("/alerts?saved=1")

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
