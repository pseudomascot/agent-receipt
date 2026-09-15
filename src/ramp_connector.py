"""Ramp connector: the agent's card program, read and controlled through Ramp's Developer API.

Ramp's primitive is the **fund**: a budget with restrictions (amount, interval,
allowed categories) that issues a virtual card automatically and can be
suspended, unsuspended or terminated in one call. That is exactly the shape
"one agent, one card, one budget" needs, so:

- **Issue**: the Agents page hands an agent a fund ("Agent · Bookkeeping bot",
  $500/month, virtual card only). The fund id ↔ agent mapping is kept in the
  `ramp_funds` table and the grant is written to the receipt.
- **Read**: every Ramp transaction becomes a `purchase` row. A transaction on a
  fund we issued is attributed to that agent *by construction* (source `log`,
  like the agent's own Google account); anything else on the Ramp account is a
  `card` row attributed by the usual keyboard/log correlation. Declines are
  recorded too — "tried to spend past its budget" is evidence.
- **Stop**: suspend / unsuspend / terminate the fund (src/stop.py).

Endpoints verified against Ramp's OpenAPI spec on 2026-09-15 (docs/RAMP.md):
POST /developer/v1/token (HTTP Basic client_id:client_secret, JSON body,
grant_type=client_credentials), GET/POST /developer/v1/funds,
POST/DELETE /developer/v1/funds/{id}/suspension, DELETE /developer/v1/funds/{id},
GET /developer/v1/transactions (from_date, page_size, page.next),
GET /developer/v1/users. Sandbox: https://demo-api.ramp.com (no real money).
Stdlib only.
"""

import base64
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from agents import INSERT as RECEIPT_INSERT
from agents import RECEIPT_AGENT
from config import ramp_settings

PAGE = 100
INTERVALS = ("DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY", "TOTAL")
STATE_WORDS = {"CLEARED": "cleared", "PENDING": "pending", "COMPLETION": "completed", "PENDING_INITIATION": "pending",
               "DECLINED": "DECLINED", "ERROR": "error"}
_tokens: dict = {}          # client_id -> (access_token, expires_at)


class RampError(Exception):
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload or {}
        msg = self.payload.get("error_v2", {}).get("message") if isinstance(self.payload.get("error_v2"), dict) else None
        super().__init__(f"{status}: {msg or self.payload.get('error') or self.payload}")


# --- HTTP --------------------------------------------------------------------

def _http(url: str, method: str, headers: dict, data: bytes | None) -> dict:
    req = urllib.request.Request(url, data=data, method=method, headers={"User-Agent": "agent-receipt", **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except ValueError:
            payload = {}
        raise RampError(exc.code, payload) from None


def token(settings: dict, http=_http) -> str:
    """Client-credentials access token, cached until shortly before it expires."""
    cached = _tokens.get(settings["client_id"])
    if cached and cached[1] > time.time() + 60:
        return cached[0]
    basic = base64.b64encode(f"{settings['client_id']}:{settings['client_secret']}".encode()).decode()
    body = json.dumps({"grant_type": "client_credentials", "scope": settings["scopes"]}).encode()
    data = http(f"{settings['api']}/developer/v1/token", "POST",
                {"Authorization": f"Basic {basic}", "Content-Type": "application/json"}, body)
    access = data["access_token"]
    _tokens[settings["client_id"]] = (access, time.time() + int(data.get("expires_in") or 3600))
    return access


def api(settings: dict, method: str, path: str, params: dict | None = None, body: dict | None = None,
        idempotent: bool = False, http=_http) -> dict:
    """One Developer API call. `path` may be a full URL (pagination's page.next)."""
    url = path if path.startswith("http") else settings["api"] + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"Authorization": f"Bearer {token(settings, http)}", "Content-Type": "application/json"}
    if idempotent:
        headers["X-Idempotency-Key"] = uuid.uuid4().hex
    data = json.dumps(body).encode() if body is not None else None
    return http(url, method, headers, data)


def list_all(settings: dict, path: str, params: dict | None = None, call=api) -> list[dict]:
    """Follow `page.next` until exhausted."""
    out, url, params = [], path, {**(params or {}), "page_size": PAGE}
    for _ in range(200):
        page = call(settings, "GET", url, params)
        out.extend(page.get("data") or [])
        url = (page.get("page") or {}).get("next")
        params = None
        if not url:
            break
    return out


# --- funds: issue, look up, control -------------------------------------------

def funds_for_agents(conn: sqlite3.Connection) -> dict:
    """fund_id -> row dict for every fund the app handed to an agent."""
    cols = ["fund_id", "agent", "display_name", "limit_amount", "currency", "interval", "card_last4", "state", "created_at", "by_user"]
    return {r[0]: dict(zip(cols, r)) for r in conn.execute(f"SELECT {', '.join(cols)} FROM ramp_funds")}


def fund_of(conn: sqlite3.Connection, agent: str) -> dict | None:
    return next((f for f in funds_for_agents(conn).values() if f["agent"] == agent and f["state"] != "TERMINATED"), None)


def issue_fund(conn: sqlite3.Connection, settings: dict, agent: str, label: str, limit: float, interval: str,
               user: str, currency: str = "USD", call=api) -> dict:
    """Give an agent its own Ramp fund (budget + virtual card). Recorded on the receipt."""
    if not settings.get("user_id"):
        raise RampError(0, {"error": "RECEIPT_RAMP_USER_ID is not set — the Ramp user who holds the agents' cards (docs/RAMP.md)"})
    interval = interval.upper()
    if interval not in INTERVALS:
        raise RampError(0, {"error": f"interval must be one of {', '.join(INTERVALS)}"})
    if limit <= 0:
        raise RampError(0, {"error": "the limit must be more than zero"})
    display = f"Agent · {label}"[:64]
    fund = call(settings, "POST", "/developer/v1/funds", body={
        "user_id": settings["user_id"],
        "display_name": display,
        "spending_restrictions": {"limit": {"amount": int(round(limit * 100)), "currency_code": currency},
                                  "interval": interval},
        "permitted_spend_types": {"virtual_card": True, "physical_card": False, "reimbursements": False},
    }, idempotent=True)
    cards = fund.get("cards") or []
    last4 = (cards[0].get("last_four") if cards else None) or ""
    now = time.time()
    conn.execute("INSERT OR REPLACE INTO ramp_funds (fund_id, agent, display_name, limit_amount, currency, interval, "
                 "card_last4, state, created_at, by_user) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (fund["id"], agent, display, limit, currency, interval, last4, fund.get("state") or "ACTIVE", now, user))
    _record(conn, "issue", agent, user,
            f"Gave {label} a Ramp card: {limit:,.2f} {currency} per {interval.lower()}"
            + (f", card •••• {last4}" if last4 else "") + f" ({settings['mode']})",
            1, "the fund can be suspended or terminated from the Stop page", {"fund_id": fund["id"]})
    return {"fund_id": fund["id"], "display_name": display, "last4": last4, "state": fund.get("state")}


def set_fund_state(conn: sqlite3.Connection, settings: dict, fund_id: str, action: str, call=api) -> dict:
    """action: suspend | unsuspend | terminate. Returns the fund as Ramp now sees it."""
    if action == "suspend":
        fund = call(settings, "POST", f"/developer/v1/funds/{fund_id}/suspension")
    elif action == "unsuspend":
        fund = call(settings, "DELETE", f"/developer/v1/funds/{fund_id}/suspension")
    elif action == "terminate":
        fund = call(settings, "DELETE", f"/developer/v1/funds/{fund_id}")
    else:
        raise RampError(0, {"error": f"unknown action {action}"})
    state = fund.get("state") or {"suspend": "SUSPENDED", "unsuspend": "ACTIVE", "terminate": "TERMINATED"}[action]
    conn.execute("UPDATE ramp_funds SET state = ? WHERE fund_id = ?", (state, fund_id))
    conn.commit()
    return fund


def refresh_funds(conn: sqlite3.Connection, settings: dict, call=api) -> int:
    """Pull state and card last-four for every fund we issued."""
    ours = funds_for_agents(conn)
    if not ours:
        return 0
    n = 0
    for fund in list_all(settings, "/developer/v1/funds", call=call):
        if fund.get("id") in ours:
            cards = fund.get("cards") or []
            last4 = (cards[0].get("last_four") if cards else None) or ours[fund["id"]]["card_last4"]
            conn.execute("UPDATE ramp_funds SET state = ?, card_last4 = ? WHERE fund_id = ?",
                         (fund.get("state") or ours[fund["id"]]["state"], last4, fund["id"]))
            n += 1
    conn.commit()
    return n


def _record(conn, event, agent, user, target, reversible, reason, extra):
    now = time.time()
    conn.execute(RECEIPT_INSERT, (
        now, RECEIPT_AGENT, user, "input", "other", target, reversible, "human",
        f"a button was pressed in Agent Receipt; reversibility: {reason}",
        json.dumps({"source": "agent-receipt", "tool": f"agent-receipt:ramp:{event}", "agent": agent, **extra}),
        f"agent-receipt:ramp:{event}:{now:.3f}:{uuid.uuid4().hex[:8]}",
    ))
    conn.commit()


# --- transactions -> receipt rows ---------------------------------------------

ROW_INSERT = """
INSERT OR IGNORE INTO actions
    (timestamp, agent, user, source, action_type, target, amount, currency, artifact_link,
     reversible, attribution, confidence_note, raw_json, source_ref)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _money(tx: dict) -> tuple[float | None, str]:
    """Ramp marks the flat `amount` deprecated; prefer the structured amounts when present."""
    for key in ("entity_amount", "merchant_amount"):
        obj = tx.get(key)
        if isinstance(obj, dict) and obj.get("amount") is not None:
            rate = obj.get("minor_unit_conversion_rate") or 100
            return obj["amount"] / rate, (obj.get("currency_code") or "USD").upper()
    if tx.get("amount") is not None:
        return float(tx["amount"]), (tx.get("currency_code") or "USD").upper()
    return None, "USD"


def _epoch(text) -> float | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def sync(conn: sqlite3.Connection, settings: dict, user: str = "", call=api, since_days: int = 30) -> dict:
    """Pull Ramp transactions since the last sync into `actions`. Returns counts."""
    ours = funds_for_agents(conn)
    row = conn.execute("SELECT value FROM meta WHERE key = 'ramp_since'").fetchone()
    since = datetime.fromtimestamp(float(row[0]), tz=timezone.utc) - timedelta(days=1) if row else \
        datetime.now(timezone.utc) - timedelta(days=since_days)
    txs = list_all(settings, "/developer/v1/transactions",
                   {"from_date": since.isoformat(timespec="seconds"), "order_by_date_asc": "true"}, call=call)
    counts = {"seen": len(txs), "new": 0, "agent": 0, "declined": 0}
    latest = float(row[0]) if row else 0.0
    for tx in txs:
        ts = _epoch(tx.get("user_transaction_time")) or _epoch(tx.get("settlement_date")) or time.time()
        latest = max(latest, ts)
        amount, currency = _money(tx)
        merchant = tx.get("merchant_name") or tx.get("merchant_descriptor") or "unknown merchant"
        state = (tx.get("state") or "").upper()
        declined = state == "DECLINED"
        fund = ours.get(tx.get("fund_id") or "")
        holder = tx.get("card_holder") or {}
        holder_name = " ".join(p for p in (holder.get("first_name"), holder.get("last_name")) if p) if isinstance(holder, dict) else ""
        link = f"{settings['app']}/transactions/{tx.get('id')}" if tx.get("id") else None
        target = ("DECLINED: " if declined else "") + merchant
        if fund:
            agent, source, attribution = fund["agent"], "log", "agent"
            note = f"by credential: Ramp fund '{fund['display_name']}' issued to this agent"
        else:
            agent, source, attribution = settings["agent"], "card", "unknown"
            note = f"Ramp transaction on the {settings['mode']} account" + (f", card holder {holder_name}" if holder_name else "")
        if declined:
            reason = ((tx.get("decline_details") or {}).get("reason") if isinstance(tx.get("decline_details"), dict) else None)
            note += f"; declined by Ramp{': ' + str(reason) if reason else ''}"
            reversible, rev = 1, "nothing was charged"
        else:
            reversible, rev = 0, "a card purchase can only be refunded by the merchant"
        cur = conn.execute(ROW_INSERT, (
            ts, agent, user, source, "purchase", target, amount, currency, link, reversible, attribution,
            f"{note}; reversibility: {rev}",
            json.dumps({"source": "ramp", "tool": "ramp:transaction", "fund_id": tx.get("fund_id"), "card_id": tx.get("card_id"),
                        "state": state, "mcc": tx.get("merchant_category_code"), "mode": settings["mode"]}),
            f"ramp:{tx.get('id')}",
        ))
        if cur.rowcount:
            counts["new"] += 1
            counts["agent"] += 1 if fund else 0
            counts["declined"] += 1 if declined else 0
        elif state:   # state moved (PENDING -> CLEARED): keep the note honest without touching attribution
            conn.execute("UPDATE actions SET raw_json = json_set(raw_json, '$.state', ?) WHERE source_ref = ? AND source IN ('card', 'log')",
                         (state, f"ramp:{tx.get('id')}"))
    if latest:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('ramp_since', ?)", (str(latest),))
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('card', ?, ?)",
                 (since.timestamp(), time.time()))
    conn.commit()
    counts["funds"] = refresh_funds(conn, settings, call=call)
    return counts


def sync_if_configured(conn: sqlite3.Connection, user: str = "") -> dict | None:
    settings = ramp_settings()
    if not settings:
        return None
    try:
        return sync(conn, settings, user)
    except (RampError, OSError, ValueError, KeyError) as exc:
        return {"error": str(exc)}


def list_users(settings: dict, call=api) -> list[dict]:
    """For docs/RAMP.md setup: which Ramp user should hold the agents' cards."""
    return [{"id": u.get("id"), "name": " ".join(p for p in (u.get("first_name"), u.get("last_name")) if p),
             "email": u.get("email"), "role": u.get("role"), "status": u.get("status")}
            for u in list_all(settings, "/developer/v1/users", call=call)]
