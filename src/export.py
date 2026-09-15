"""CSV export of a statement, with a footer that lets a recipient verify it.

The data section (header + rows) is hashed with SHA-256. The footer states the
hash and the exact command to recompute it, so anyone holding the file can tell
whether the rows were altered after export.
"""

import csv
import hashlib
import io
from datetime import datetime

from queries import coverage_notes

COLUMNS = ["timestamp", "date", "time", "agent", "user", "project", "action_type", "what", "target",
           "reversible", "reversible_reason", "attribution", "attribution_note",
           "artifact_link", "amount", "currency", "source_ref"]


def export_csv(conn, stats: dict, filters: dict) -> str:
    rows = _rows(conn, stats)
    data = io.StringIO(newline="")
    writer = csv.writer(data, lineterminator="\n")
    writer.writerow(COLUMNS)
    for r in rows:
        writer.writerow(r)
    data_text = data.getvalue()
    digest = hashlib.sha256(data_text.encode("utf-8")).hexdigest()
    line_count = len(rows) + 1

    footer = io.StringIO(newline="")
    fw = csv.writer(footer, lineterminator="\n")
    fw.writerow([])
    fw.writerow(["# Agent Receipt export", f"generated {datetime.now().isoformat(timespec='seconds')}"])
    fw.writerow(["# range", stats["label"]])
    fw.writerow(["# filters", ", ".join(f"{k}={v}" for k, v in filters.items() if v) or "none"])
    fw.writerow(["# rows", len(rows)])
    for name, status, detail in coverage_notes():
        fw.writerow([f"# coverage: {name}", status, detail])
    fw.writerow(["# input monitor", f"about {stats['monitor_minutes']} min in this range; "
                 f"{stats['uncovered']} of {stats['total']} actions happened while it was off"])
    fw.writerow(["# sha256 of the data section (first %d lines)" % line_count, digest])
    fw.writerow(["# verify", f"head -n {line_count} <this file> | shasum -a 256"])
    return data_text + footer.getvalue()


def _rows(conn, stats: dict):
    ids = [a["id"] for a in stats["shown"]]
    if not ids:
        return []
    placeholders = ",".join("?" * len(ids))
    full = {r[0]: r[1] for r in conn.execute(
        f"SELECT id, target FROM actions WHERE id IN ({placeholders})", ids)}
    out = []
    for a in stats["shown"]:
        when = datetime.fromtimestamp(a["timestamp"])
        out.append([
            when.isoformat(timespec="seconds"), when.date().isoformat(), when.strftime("%H:%M:%S"),
            a["agent"], a["user"], a["project"], a["action_type"], a.get("what", ""), full.get(a["id"], a["target"]),
            a["reversible"], a["reversible_reason"], a["attribution"], a["note"].split(" | ")[-1],
            a["artifact_link"] or "", a["amount"] if a["amount"] is not None else "",
            a["currency"] or "", a.get("source_ref") or "",
        ])
    return out


# ---------------------------------------------------------------------------
# Bookkeeping export: the three-column layout QuickBooks and Xero accept as a
# bank-statement import (Date, Description, Amount). Money out is negative.
# One currency per file. No footer — the importers reject extra rows — so the
# verifiable copy of the same rows is the full export.csv.
# ---------------------------------------------------------------------------

MONEY_COLUMNS = ["Date", "Description", "Amount"]


def money_description(r: dict) -> str:
    what = r.get("what") or r.get("target") or ""
    if what.startswith("Moved money: "):                                  # say which way it moved
        what = ("Paid: " if r.get("direction") == "out" else "Received: ") + what[len("Moved money: "):]
    parts = [what, f"agent: {r['agent']}"]
    project = r.get("project") or ""
    if project and not project.startswith("("):                            # skip "(unknown project)"
        parts.append(f"project: {project}")
    who = {"agent": "done by the agent", "human": "a person was at the keyboard",
           "unknown": "unexplained"}.get(r.get("attribution"), "")
    if who:
        parts.append(who)
    if r.get("source_ref"):
        parts.append(f"ref: {r['source_ref']}")
    return " · ".join(p for p in parts if p)[:250]


def money_csv(money: dict, currency: str) -> str:
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(MONEY_COLUMNS)
    for r in sorted(money["rows"], key=lambda r: r["timestamp"]):       # chronological
        if r["currency"] != currency:
            continue
        when = datetime.fromtimestamp(r["timestamp"])
        writer.writerow([when.strftime("%m/%d/%Y"), money_description(r), f"{r['signed']:.2f}"])
    return out.getvalue()
