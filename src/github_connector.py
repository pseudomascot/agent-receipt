"""GitHub connector: what an agent's own GitHub account committed and opened.

Cloud coding agents (Claude Code on the web, Cursor cloud agents, Codex in
the cloud, anything hosted) leave no log on this Mac — but they finish their
work the same way: commits and pull requests. If an agent has **its own
GitHub identity** (the same principle as its own mailbox and card), every
commit and pull request that identity makes is evidence the receipt can read,
attributed by credential, from an API that is stable and documented.

Only the accounts listed in RECEIPT_GITHUB_AGENT_LOGINS are read. A person's
own commits are their business — and a local agent's commits are already on
the receipt from its transcript, so reading everyone would double-count.

Rows: one `file_write` per commit ("repo@sha7: first line of the message —
N files"), linked to the commit, marked not undoable (it has been published);
one `post` per pull request opened. Endpoints (GitHub REST, verified 2026-09-16):
GET /user, GET /user/repos, GET /repos/{o}/{r}/commits?author=&since=,
GET /repos/{o}/{r}/commits/{sha}, GET /repos/{o}/{r}/pulls?state=all&sort=updated.
Stdlib only; read-only.
"""

import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from config import github_settings

API = "https://api.github.com"
PAGE = 100
MAX_PAGES = 5
MAX_REPOS = 200
MAX_FILES_LISTED = 6


class GitHubError(Exception):
    def __init__(self, status, payload):
        self.status = status
        self.payload = payload or {}
        super().__init__(f"{status}: {self.payload.get('message') or self.payload}")


def _http(url: str, headers: dict) -> tuple[dict | list, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "agent-receipt", **headers})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return (json.loads(raw) if raw else {}), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except ValueError:
            payload = {}
        raise GitHubError(exc.code, payload) from None


def api(settings: dict, path: str, params: dict | None = None, http=_http):
    url = path if path.startswith("http") else API + path
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    headers = {"Authorization": f"Bearer {settings['token']}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    data, _ = http(url, headers)
    return data


def paged(settings: dict, path: str, params: dict | None = None, call=api, max_pages: int = MAX_PAGES) -> list:
    out = []
    for page in range(1, max_pages + 1):
        chunk = call(settings, path, {**(params or {}), "per_page": PAGE, "page": page})
        if not isinstance(chunk, list):
            break
        out.extend(chunk)
        if len(chunk) < PAGE:
            break
    return out


def repos_to_watch(settings: dict, call=api) -> list[str]:
    """Explicit RECEIPT_GITHUB_REPOS, else every repo the token can see (owner, collaborator, org member)."""
    if settings.get("repos"):
        return settings["repos"]
    found = paged(settings, "/user/repos", {"affiliation": "owner,collaborator,organization_member", "sort": "pushed"},
                  call=call, max_pages=2)
    return [r["full_name"] for r in found if isinstance(r, dict) and r.get("full_name")][:MAX_REPOS]


def whoami(settings: dict, call=api) -> dict:
    me = call(settings, "/user")
    return {"login": me.get("login"), "name": me.get("name")}


# --- sync --------------------------------------------------------------------------

INSERT = """
INSERT OR IGNORE INTO actions
    (timestamp, agent, user, source, action_type, target, artifact_link, reversible,
     attribution, confidence_note, raw_json, source_ref)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _epoch(text) -> float | None:
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _login(obj) -> str:
    return ((obj or {}).get("login") or "") if isinstance(obj, dict) else ""


def sync(conn: sqlite3.Connection, settings: dict, user: str = "", call=api, since_days: int = 30) -> dict:
    """Pull commits and pull requests by the agents' GitHub accounts into `actions`."""
    logins = [l.lower() for l in settings["agent_logins"]]
    row = conn.execute("SELECT value FROM meta WHERE key = 'github_since'").fetchone()
    started = time.time()
    since_dt = (datetime.fromtimestamp(float(row[0]), tz=timezone.utc) - timedelta(days=2)) if row \
        else datetime.now(timezone.utc) - timedelta(days=since_days)
    since = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    counts = {"repos": 0, "commits": 0, "pulls": 0, "new": 0}
    for repo in repos_to_watch(settings, call=call):
        counts["repos"] += 1
        for login in settings["agent_logins"]:
            for c in paged(settings, f"/repos/{repo}/commits", {"author": login, "since": since}, call=call):
                counts["commits"] += 1
                counts["new"] += _commit_row(conn, settings, repo, login, c, user, call)
        for pr in paged(settings, f"/repos/{repo}/pulls", {"state": "all", "sort": "updated", "direction": "desc"},
                        call=call, max_pages=1):
            if _login(pr.get("user")).lower() not in logins or (_epoch(pr.get("created_at")) or 0) < since_dt.timestamp():
                continue
            counts["pulls"] += 1
            counts["new"] += _pr_row(conn, settings, repo, pr, user)
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('github_since', ?)", (str(started),))
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('log', ?, ?)", (since_dt.timestamp(), started))
    conn.commit()
    return counts


def _commit_row(conn, settings, repo: str, login: str, c: dict, user: str, call) -> int:
    sha = c.get("sha") or ""
    commit = c.get("commit") or {}
    ts = _epoch((commit.get("author") or {}).get("date")) or _epoch((commit.get("committer") or {}).get("date")) or time.time()
    message = (commit.get("message") or "").strip().splitlines()[0] if commit.get("message") else "(no message)"
    files = []
    try:
        detail = call(settings, f"/repos/{repo}/commits/{sha}")
        files = [f.get("filename") for f in (detail.get("files") or []) if isinstance(f, dict) and f.get("filename")]
    except (GitHubError, OSError, ValueError):
        pass
    listed = ", ".join(files[:MAX_FILES_LISTED]) + (f" … +{len(files) - MAX_FILES_LISTED} more" if len(files) > MAX_FILES_LISTED else "")
    target = f"{repo}@{sha[:7]}: {message}" + (f" — {len(files)} file{'s' if len(files) != 1 else ''}: {listed}" if files else "")
    cur = conn.execute(INSERT, (
        ts, f"github ({login})", user, "log", "file_write", target, c.get("html_url"), 0, "agent",
        f"by credential: GitHub account {login} (an agent's) authored this commit; reversibility: "
        "published to GitHub — a new commit can undo the change, but it has been visible to others",
        json.dumps({"source": "github", "tool": "github:commit", "repo": repo, "sha": sha, "files": files[:50], "login": login}),
        f"github:commit:{sha}",
    ))
    return int(cur.rowcount > 0)


def _pr_row(conn, settings, repo: str, pr: dict, user: str) -> int:
    login = _login(pr.get("user"))
    number = pr.get("number")
    ts = _epoch(pr.get("created_at")) or time.time()
    state = "merged" if pr.get("merged_at") else (pr.get("state") or "open")
    target = f"{repo}#{number}: {(pr.get('title') or '').strip()} ({state})"
    cur = conn.execute(INSERT, (
        ts, f"github ({login})", user, "log", "post", target, pr.get("html_url"), 1, "agent",
        f"by credential: GitHub account {login} (an agent's) opened this pull request; reversibility: a pull request can be closed",
        json.dumps({"source": "github", "tool": "github:pull_request", "repo": repo, "number": number, "state": state, "login": login}),
        f"github:pr:{repo}#{number}",
    ))
    return int(cur.rowcount > 0)


def sync_if_configured(conn: sqlite3.Connection, user: str = "") -> dict | None:
    settings = github_settings()
    if not settings or not settings["agent_logins"]:
        return None
    try:
        return sync(conn, settings, user)
    except (GitHubError, OSError, ValueError, KeyError) as exc:
        return {"error": str(exc)}
