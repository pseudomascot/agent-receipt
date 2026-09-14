"""Daily summary: a short plain-text statement per day, written to a file.

Generated from the database only. No AI, no network. This is the text that
v2 will deliver by SMS or email.
"""

import sqlite3
import sys
from datetime import date
from pathlib import Path

from queries import NOT_COVERED, day_statement
from store import DB_PATH, connect

SUMMARIES_DIR = Path(__file__).resolve().parent.parent / "summaries"


def build_summary(conn: sqlite3.Connection, day: date) -> str:
    s = day_statement(conn, day)
    a = s["by_attribution"]
    lines = [f"Agent Receipt — {s['day']}"]

    if s["total"] == 0:
        lines.append("No agent actions recorded.")
    else:
        lines.append(f"{s['total']} actions: {a['agent']} agent, {a['human']} human, {a['unknown']} unknown.")
        lines.append("By type: " + ", ".join(f"{n} {t}" for t, n in s["by_type"]) + ".")
        lines.append("Projects: " + ", ".join(f"{p} ({n})" for p, n in s["by_project"]) + ".")
        if len(s["by_agent"]) > 1:
            lines.append("Agents: " + ", ".join(f"{a} ({n})" for a, n in s["by_agent"]) + ".")
        if len(s["by_user"]) > 1:
            lines.append("Users: " + ", ".join(f"{u} ({n})" for u, n in s["by_user"]) + ".")
        if s["money"]:
            lines.append("Money: " + ", ".join(f"{amt:.2f} {cur}" for cur, amt in s["money"].items()) + ".")
        else:
            lines.append("Money: none recorded.")
        if s["top_files"]:
            lines.append("Most-written files: " + "; ".join(
                f"{Path(t).name} ({n}x)" if n > 1 else Path(t).name for t, n in s["top_files"]) + ".")

    if s["monitor_intervals"]:
        spans = ", ".join(f"{b}–{e}" for b, e in s["monitor_intervals"])
        lines.append(f"Input monitor: on for about {s['monitor_minutes']} min ({spans}).")
    else:
        lines.append("Input monitor: off all day.")
    if s["total"]:
        lines.append(f"{s['uncovered']} of {s['total']} actions happened while the monitor was off.")

    if s["unknown"]:
        lines.append(f"Unknown: {len(s['unknown'])} action(s) nobody can be confirmed for — review them.")
    else:
        lines.append("Unknown: none.")

    lines.append("Not covered: " + "; ".join(NOT_COVERED) + ".")
    return "\n".join(lines) + "\n"


def write_summary(conn: sqlite3.Connection, day: date, out_dir: Path = SUMMARIES_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{day.isoformat()}.txt"
    path.write_text(build_summary(conn, day), encoding="utf-8")
    return path


def run(db_path: Path = DB_PATH, day: date | None = None) -> None:
    conn = connect(db_path)
    day = day or date.today()
    path = write_summary(conn, day)
    print(path.read_text(encoding="utf-8"), end="")
    print(f"(saved to {path})")
    conn.close()


if __name__ == "__main__":
    run(day=date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else None)
