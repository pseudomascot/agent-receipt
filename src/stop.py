"""The Stop panel: take away what an agent acts with, where the issuer lets us.

Retiring an agent (agents.py) records a decision. Stopping one means reaching
the thing it acts with — its card, its login, its running process. Three kinds
of control live here:

- "api":     the app makes the call itself. Stripe Issuing cards can be frozen or
             cancelled; the receipt's own Google token can be revoked.
- "process": the app ends the agent's running processes on this Mac (Codex, the
             Claude Code CLI). That stops it now, not forever.
- "link":    the issuer has no API for it (a consumer Gmail login, a pasted Stripe
             key, Cowork's schedule, a macOS permission). The button opens the
             right page and lists the steps; the person does the rest.

Every press — success or failure — is written to the receipt as a human action,
so "who pulled the plug, when, and did it work" is on the record like everything
else. Nothing here ever deletes history.
"""

import json
import sqlite3
import subprocess
import time
import uuid
from pathlib import Path

from agents import INSERT, RECEIPT_AGENT
from config import (calendar_settings, email_settings, github_settings, google_calendar_settings, load_env,
                    ramp_settings, stripe_settings)
from google_calendar import TOKEN_PATH, _post_form, load_token
from ramp_connector import RampError, funds_for_agents, set_fund_state
from stripe_connector import StripeError, request

REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GOOGLE_SECURITY = "https://myaccount.google.com/security"
GOOGLE_APP_PASSWORDS = "https://myaccount.google.com/apppasswords"
WORKSPACE_USERS = "https://admin.google.com/ac/users"
MAC_AUTOMATION = "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"

# effect: what pressing it actually achieves. Shown as a pill so nobody mistakes
# "the receipt goes blind" for "the agent is stopped".
STOPS, BLINDS, PAUSES = "stops the agent", "blinds the receipt", "stops it for now"


def _control(id, agent, title, mode, effect, detail, url=None, steps=(), danger=False, reversible=1, reason=""):
    return {"id": id, "agent": agent, "title": title, "mode": mode, "effect": effect, "detail": detail,
            "url": url, "steps": list(steps), "danger": danger, "reversible": reversible, "reason": reason}


def controls(env: dict | None = None, conn: sqlite3.Connection | None = None) -> list[dict]:
    """Every control that applies to this machine's configuration. No secrets in the output.

    `conn` adds one suspend/unsuspend/terminate trio per Ramp fund the app handed to an agent."""
    env = env if env is not None else load_env()
    out = []

    ramp = ramp_settings(env)
    if ramp and conn is not None:
        for fund in funds_for_agents(conn).values():
            if fund["state"] == "TERMINATED":
                continue
            card = f" (card •••• {fund['card_last4']})" if fund["card_last4"] else ""
            budget = f"{fund['limit_amount']:,.2f} {fund['currency']} per {(fund['interval'] or '').lower()}"
            if fund["state"] == "SUSPENDED":
                out.append(_control(
                    f"ramp_unsuspend:{fund['fund_id']}", fund["agent"], "Unsuspend the agent's Ramp card", "api", STOPS,
                    f"Lifts the suspension on this agent's fund{card}; purchases work again within its budget of {budget}.",
                    reason="it can be suspended again"))
            else:
                out.append(_control(
                    f"ramp_suspend:{fund['fund_id']}", fund["agent"], "Suspend the agent's Ramp card", "api", STOPS,
                    f"Suspends this agent's Ramp fund{card} — every purchase on it declines from this moment. Budget was {budget}.",
                    reason="the fund can be unsuspended from this page"))
            out.append(_control(
                f"ramp_terminate:{fund['fund_id']}", fund["agent"], "Terminate the agent's Ramp card (permanent)", "api", STOPS,
                f"Terminates the fund{card}. A terminated fund cannot come back; a new one has to be issued.",
                danger=True, reversible=0, reason="a terminated fund is gone for good"))

    mail = email_settings(env)
    if mail:
        gmail = "gmail" in (mail["host"] or "").lower() or mail["user"].lower().endswith("@gmail.com")
        out.append(_control(
            "mailbox_signout", mail["agent"], "Sign the mailbox out everywhere and revoke its app passwords", "link", STOPS,
            f"Google has no API for a personal account, so this is done by hand while signed in as {mail['user']}. "
            "Once the app password is gone the agent can neither read nor send from this mailbox — and neither can the receipt.",
            GOOGLE_SECURITY if gmail else None,
            [f"Sign in to Google as {mail['user']} (not your own account).",
             "Security → Your devices → Manage all devices → sign out of everything you don't recognise.",
             f"Open {GOOGLE_APP_PASSWORDS} and remove every app password (the agent's and the receipt's).",
             "Change the account password so nothing cached keeps working."]
            if gmail else ["Open your mail provider's security page for this mailbox.",
                           "Revoke the app password or IMAP/SMTP credential the agent uses.",
                           "Change the mailbox password."],
            reason="a new app password can be issued later"))
        out.append(_control(
            "mailbox_suspend", mail["agent"], "Suspend the account (Google Workspace only)", "link", STOPS,
            "If this mailbox is a Google Workspace user, an admin can suspend it in one click — mail, calendar, "
            "app passwords and sessions all stop at once. Personal Gmail has no equivalent.",
            WORKSPACE_USERS, [f"Admin console → Users → {mail['user']} → Suspend user."],
            reason="an admin can reactivate the user"))

    stripe = stripe_settings(env)
    if stripe:
        dash = "https://dashboard.stripe.com/test" if stripe["mode"] == "test" else "https://dashboard.stripe.com"
        out.append(_control(
            "stripe_freeze", stripe["agent"], "Freeze the agent's Issuing cards", "api", STOPS,
            "Sets every active Issuing card on this Stripe account to inactive. Purchases decline immediately. "
            "Applies only if the account has Issuing cards; charges the agent *collects* are unaffected.",
            reason="a frozen card can be made active again in Stripe"))
        out.append(_control(
            "stripe_cancel", stripe["agent"], "Cancel the agent's Issuing cards (permanent)", "api", STOPS,
            "Cancels every active Issuing card. A cancelled card can never be reactivated — a new one has to be issued.",
            danger=True, reversible=0, reason="a cancelled card is gone for good"))
        out.append(_control(
            "stripe_key", stripe["agent"], "Roll the Stripe secret key", "link", STOPS,
            "Stripe keys can't be rolled by API. Rolling the key logs the agent out of Stripe entirely "
            f"({stripe['mode']} mode); the receipt loses its read access too until the new key is pasted into .env.",
            f"{dash}/apikeys",
            ["Developers → API keys → the key the agent uses → ⋯ → Roll key.",
             "Paste the new key into the receipt's .env yourself (RECEIPT_STRIPE_TEST_KEY or RECEIPT_STRIPE_KEY) if you still want it to read Stripe.",
             "Give the agent a new, more restricted key only if you want it back."],
            reason="a new key can be issued"))

    google = google_calendar_settings(env)
    if google:
        for email in google["agent_emails"]:
            out.append(_control(
                f"google_signout:{email}", f"google calendar ({email})", "Sign the agent's Google account out everywhere", "link", STOPS,
                "Google has no API for a personal account: sign in as the agent and end its sessions and passwords by hand.",
                GOOGLE_SECURITY,
                [f"Sign in to Google as {email}.",
                 "Security → Your devices → sign out of everything; Third-party apps → remove anything the agent uses.",
                 f"Remove its app passwords at {GOOGLE_APP_PASSWORDS} and change the password."],
                reason="the account can be set up again"))
            out.append(_control(
                f"google_suspend:{email}", f"google calendar ({email})", "Suspend the account (Google Workspace only)", "link", STOPS,
                "For a Workspace user an admin suspends it in one click; everything stops at once.",
                WORKSPACE_USERS, [f"Admin console → Users → {email} → Suspend user."],
                reason="an admin can reactivate the user"))
        out.append(_control(
            "google_token", google["agent"], "Revoke the receipt's own Google access", "api", BLINDS,
            "Revokes the read-only token the receipt uses to watch Google Calendar and deletes it from this Mac. "
            "The agent's account is untouched — only the receipt stops seeing it.",
            reason="run examples/google_calendar_auth.py to sign in again"))

    github = github_settings(env)
    if github:
        for login in github["agent_logins"]:
            out.append(_control(
                f"github_revoke:{login}", f"github ({login})", "Cut the agent's GitHub account off", "link", STOPS,
                "GitHub has no API to suspend someone else's account, but you can take away everything it can reach: "
                "remove it from your repositories and organisation, and revoke its tokens if you hold that login.",
                f"https://github.com/{login}",
                [f"In each repository: Settings → Collaborators → remove {login} (or Organisation → People → remove).",
                 f"If you control the {login} account: sign in as it → Settings → Developer settings → revoke its tokens and SSH keys.",
                 "Revoke any GitHub App or OAuth authorisation the agent uses."],
                reason="access can be granted again"))
    if calendar_settings(env):
        out.append(_control(
            "mac_calendar", calendar_settings(env)["agent"], "Take away Agent Receipt's Calendar permission", "link", BLINDS,
            "macOS permissions can't be changed by an app. This stops the receipt watching the Mac Calendar; "
            "it does not stop any agent.",
            MAC_AUTOMATION, ["System Settings → Privacy & Security → Automation → Agent Receipt → untick Calendar."],
            reason="tick it again to resume"))

    out.append(_control(
        "codex_processes", "codex", "End running Codex processes", "process", PAUSES,
        "Ends every `codex` process on this Mac right now. Anything it was doing stops mid-way; it can be started again.",
        reason="Codex can be launched again"))
    out.append(_control(
        "claude_processes", "claude-code", "End running Claude Code sessions", "process", PAUSES,
        "Ends every `claude` process — terminal sessions and the Claude app's Code tab alike, including any session "
        "you are talking to right now. Cowork and scheduled tasks are not affected.",
        reason="sessions can be started again"))
    out.append(_control(
        "cowork_schedule", "cowork (scheduled task)", "Pause Cowork's scheduled tasks", "link", STOPS,
        "Scheduled tasks run inside the Claude app with nobody present; there is no outside switch for them.",
        None, ["Open the Claude app → Cowork → Scheduled tasks.", "Pause or delete each task you want stopped.",
               "Quit the Claude app to stop everything Cowork is doing this minute."],
        reason="tasks can be resumed"))
    return out


def find(control_id: str, env: dict | None = None, conn: sqlite3.Connection | None = None) -> dict | None:
    return next((c for c in controls(env, conn) if c["id"] == control_id), None)


# --- doing it ------------------------------------------------------------------

def _running(name: str) -> int:
    try:
        out = subprocess.run(["pgrep", "-x", name], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return 0
    return len([line for line in out.splitlines() if line.strip()])


def _end(name: str) -> int:
    before = _running(name)
    if before:
        try:
            subprocess.run(["pkill", "-x", name], capture_output=True, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return before


def run(conn: sqlite3.Connection, control_id: str, user: str, env: dict | None = None,
        stripe_api=request, post=_post_form, token_path: Path = TOKEN_PATH, end_processes=_end, ramp_call=None) -> dict:
    """Press one control (or "all"). Returns {"ok", "message", "results"} and records every press."""
    env = env if env is not None else load_env()
    if ramp_call is None:
        from ramp_connector import api as ramp_call
    if control_id == "all":
        results = []
        for c in controls(env, conn):
            if c["mode"] in ("api", "process") and not c["danger"]:
                results.append(run(conn, c["id"], user, env, stripe_api, post, token_path, end_processes, ramp_call))
        ok = all(r["ok"] for r in results) if results else False
        message = "; ".join(r["message"] for r in results) or "nothing on this Mac can be stopped by the app itself"
        _record(conn, "all", None, user, "Freeze all: " + message, ok, 1, "each step can be undone on its own")
        return {"ok": ok, "message": message, "results": results}

    c = find(control_id, env, conn)
    if c is None:
        return {"ok": False, "message": "no such control", "results": []}
    if c["mode"] == "link":
        _record(conn, c["id"], c["agent"], user, f"Opened the steps for: {c['title']}", True, 1, "nothing changed yet")
        return {"ok": True, "message": f"opened the steps for “{c['title']}” — the rest is done on that page", "results": []}
    try:
        if c["id"] in ("stripe_freeze", "stripe_cancel"):
            message, ok = _stripe_cards(env, "inactive" if c["id"] == "stripe_freeze" else "canceled", stripe_api)
        elif c["id"] == "google_token":
            message, ok = _google_revoke(post, token_path)
        elif c["id"].startswith(("ramp_suspend:", "ramp_unsuspend:", "ramp_terminate:")):
            action, fund_id = c["id"].split(":", 1)
            action = action[len("ramp_"):]
            fund = set_fund_state(conn, ramp_settings(env), fund_id, action, call=ramp_call)
            verb = {"suspend": "suspended", "unsuspend": "unsuspended", "terminate": "terminated"}[action]
            message, ok = f"{verb} the Ramp fund — Ramp now reports it {(fund.get('state') or verb).lower()}", True
        elif c["id"] == "codex_processes":
            n = end_processes("codex")
            message, ok = (f"ended {n} Codex process{'es' if n != 1 else ''}" if n else "no Codex process was running"), True
        elif c["id"] == "claude_processes":
            n = end_processes("claude")
            message, ok = (f"ended {n} Claude Code session{'s' if n != 1 else ''}" if n else "no Claude Code session was running"), True
        else:
            message, ok = "this control has no action", False
    except StripeError as exc:
        message, ok = f"Stripe refused: {exc}", False
    except RampError as exc:
        message, ok = f"Ramp refused: {exc}", False
    except Exception as exc:  # noqa: BLE001 - a failed press must still be recorded
        message, ok = f"failed: {exc}", False
    _record(conn, c["id"], c["agent"], user, f"{c['title']}: {message}", ok, c["reversible"], c["reason"])
    return {"ok": ok, "message": message, "results": []}


def _stripe_cards(env: dict, status: str, api) -> tuple[str, bool]:
    key = stripe_settings(env)["key"]
    cards = api(key, "GET", "/v1/issuing/cards", {"status": "active", "limit": 100}).get("data") or []
    if not cards:
        return "no active Issuing cards on this Stripe account — nothing to freeze", True
    done = []
    for card in cards:
        api(key, "POST", f"/v1/issuing/cards/{card['id']}", {"status": status})
        done.append(f"•••• {card.get('last4', '????')}")
    verb = "froze" if status == "inactive" else "cancelled"
    return f"{verb} {len(done)} card{'s' if len(done) != 1 else ''} ({', '.join(done)})", True


def _google_revoke(post, token_path: Path) -> tuple[str, bool]:
    token = load_token(token_path)
    if not token:
        return "the receipt had no Google token to revoke", True
    secret = token.get("refresh_token") or token.get("access_token")
    if secret:
        post(REVOKE_URL, {"token": secret})
    try:
        token_path.unlink()
    except OSError:
        pass
    return "revoked the receipt's Google token and deleted it from this Mac", True


def _record(conn, control_id: str, agent, user: str, target: str, ok: bool, reversible: int, reason: str) -> int:
    now = time.time()
    outcome = "it worked" if ok else "it did not work"
    cur = conn.execute(INSERT, (
        now, RECEIPT_AGENT, user, "input", "other", target, reversible, "human",
        f"a Stop button was pressed in Agent Receipt — {outcome}; reversibility: {reason}",
        json.dumps({"source": "agent-receipt", "tool": f"agent-receipt:stop:{control_id}", "agent": agent,
                    "control": control_id, "ok": ok}),
        f"agent-receipt:stop:{control_id}:{now:.3f}:{uuid.uuid4().hex[:8]}",
    ))
    conn.commit()
    return cur.lastrowid


def presses(conn: sqlite3.Connection, limit: int = 30) -> list[dict]:
    """Recent Stop presses, newest first, for the panel."""
    from datetime import datetime
    out = []
    for action_id, ts, user, target, raw in conn.execute(
        "SELECT id, timestamp, user, target, raw_json FROM actions WHERE agent = ? AND raw_json LIKE '%agent-receipt:stop:%' "
        "ORDER BY timestamp DESC LIMIT ?", (RECEIPT_AGENT, limit),
    ):
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            data = {}
        out.append({"id": action_id, "when": datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M"),
                    "day": datetime.fromtimestamp(ts).date().isoformat(), "user": user or "",
                    "what": target, "ok": bool(data.get("ok")), "agent": data.get("agent") or ""})
    return out
