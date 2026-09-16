"""First-run and health report: what this machine has, and what is missing.

Shown by Setup.command, by `python src/doctor.py`, and at the bottom of the
statement's front page. Plain words, no jargon: the person reading it has
just installed the thing.
"""

import sqlite3
import sys
import time
from pathlib import Path

from config import calendar_configured, email_configured, google_calendar_configured, ramp_configured, stripe_configured
from log_parser import SOURCES, find_transcripts
from store import DB_PATH

MONITOR_STALE_SECONDS = 30
SOURCE_LABELS = {
    "claude-code": "Claude Code",
    "cowork": "Cowork (Claude Desktop local agent mode)",
    "codex": "Codex (OpenAI)",
    "cursor": "Cursor (agent conversations)",
    "inbox": "Receipt-line inbox (other agents)",
}


def accessibility_granted():
    """True/False for the app running this process (Terminal, Claude, …); None if unknown."""
    if sys.platform != "darwin":
        return None
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def status(db_path: Path = DB_PATH, sources=SOURCES) -> dict:
    found = []
    for source in sources:
        files = find_transcripts(source=source)
        found.append({
            "name": source.name,
            "label": SOURCE_LABELS.get(source.name, source.name),
            "root": str(source.root),
            "exists": source.root.exists(),
            "files": len(files),
            "mb": round(sum(f.stat().st_size for f in files) / 1048576, 1),
        })
    db = {"exists": db_path.exists(), "actions": 0, "needs_review": 0, "monitor_running": False,
          "monitor_last_seen": None, "integrity_checked": None}
    if db_path.exists():
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            db["actions"] = conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0]
            db["needs_review"] = conn.execute(
                "SELECT COUNT(*) FROM alerts al JOIN actions ac ON ac.id = al.action_id WHERE al.seen_at IS NULL"
            ).fetchone()[0]
            row = conn.execute("SELECT MAX(ended_at) FROM coverage WHERE source = 'input'").fetchone()
            if row and row[0]:
                db["monitor_last_seen"] = row[0]
                db["monitor_running"] = time.time() - row[0] < MONITOR_STALE_SECONDS
            row = conn.execute("SELECT value FROM meta WHERE key = 'integrity'").fetchone()
            db["integrity_checked"] = bool(row)
        except sqlite3.Error:
            pass
        finally:
            conn.close()
    return {
        "python": ".".join(str(v) for v in sys.version_info[:3]),
        "python_ok": sys.version_info >= (3, 11),
        "sources": found,
        "email": email_configured(),
        "stripe": stripe_configured(),
        "calendar": calendar_configured(),
        "google_calendar": google_calendar_configured(),
        "ramp": ramp_configured(),
        "accessibility": accessibility_granted(),
        "db": db,
    }


def report(s: dict) -> str:
    lines = ["Agent Receipt — status", ""]
    lines.append(f"Python {s['python']}" + ("" if s["python_ok"] else "  (3.11 or newer is required)"))
    lines.append("")
    lines.append("Agents found on this machine:")
    any_found = False
    for src in s["sources"]:
        if src["files"]:
            any_found = True
            lines.append(f"  ✓ {src['label']}: {src['files']} transcript(s), {src['mb']} MB")
        elif src["name"] == "inbox":
            lines.append(f"  · {src['label']}: empty (see docs/RECEIPT_LINE.md)")
        else:
            lines.append(f"  · {src['label']}: not found")
    if not any_found:
        lines.append("  (none yet — the receipt will fill in once an agent runs)")
    lines.append("")
    lines.append("Connectors:")
    lines.append(f"  {'✓' if s['email'] else '·'} Agent mailbox (email + card alerts): "
                 f"{'configured' if s['email'] else 'not set up — Settings page or docs/EMAIL.md'}")
    lines.append(f"  {'✓' if s['stripe'] else '·'} Stripe: {'configured' if s['stripe'] else 'not set up — Settings page or docs/STRIPE.md'}")
    lines.append(f"  {'✓' if s.get('calendar') else '·'} Calendar (Mac): {'on' if s.get('calendar') else 'off — set RECEIPT_CALENDAR=on (docs/CALENDAR.md)'}")
    lines.append(f"  {'✓' if s.get('google_calendar') else '·'} Google Calendar: {'configured' if s.get('google_calendar') else 'not set up — Settings page or docs/GOOGLE_CALENDAR.md'}")
    lines.append(f"  {'✓' if s.get('ramp') else '·'} Ramp (agent cards): {'configured' if s.get('ramp') else 'not set up — Settings page or docs/RAMP.md'}")
    lines.append("")
    acc = s["accessibility"]
    if acc is True:
        lines.append("Input monitor permission: granted for the app running this.")
    elif acc is False:
        lines.append("Input monitor permission: NOT granted. System Settings → Privacy & Security → Accessibility → "
                     "turn on the app you launch Agent Receipt from (usually Terminal).")
    else:
        lines.append("Input monitor permission: could not check (not macOS, or the check is unavailable).")
    db = s["db"]
    lines.append("")
    if not db["exists"]:
        lines.append("Database: not created yet (it appears on first run).")
    else:
        lines.append(f"Database: {db['actions']} actions, {db['needs_review']} waiting in Needs review, "
                     f"integrity {'checked' if db['integrity_checked'] else 'not checked yet'}.")
        if db["monitor_running"]:
            lines.append("Runner: input monitor is running now.")
        elif db["monitor_last_seen"]:
            lines.append("Runner: not running (last seen " + time.strftime("%Y-%m-%d %H:%M", time.localtime(db["monitor_last_seen"])) + ").")
        else:
            lines.append("Runner: has not run yet.")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report(status()))
