import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import github_connector as gh  # noqa: E402
from agents import display, known_identities  # noqa: E402
from config import github_settings  # noqa: E402
from correlator import correlate  # noqa: E402
from stop import controls  # noqa: E402
from store import connect  # noqa: E402

ENV = {"RECEIPT_GITHUB_TOKEN": "ghp_secretzz", "RECEIPT_GITHUB_AGENT_LOGINS": "acme-bot, @acme-reviewer",
       "RECEIPT_GITHUB_REPOS": "acme/site, bad-entry"}


def test_settings_parse():
    s = github_settings(ENV)
    assert s["agent_logins"] == ["acme-bot", "acme-reviewer"] and s["repos"] == ["acme/site"]
    assert github_settings({"RECEIPT_GITHUB_TOKEN": "PASTE-TOKEN"}) is None and github_settings({}) is None


def test_api_headers_and_paging():
    calls = []
    def http(url, headers):
        calls.append((url, headers))
        from urllib.parse import parse_qs, urlparse
        n = int(parse_qs(urlparse(url).query).get("page", ["1"])[0])
        return ([{"i": n}] * (gh.PAGE if n == 1 else 3)), {}
    s = github_settings(ENV)
    out = gh.paged(s, "/repos/acme/site/commits", {"author": "acme-bot"}, call=lambda st, path, params=None: gh.api(st, path, params, http=http))
    assert len(out) == gh.PAGE + 3 and calls[0][0].startswith("https://api.github.com/repos/acme/site/commits?author=acme-bot&per_page=100&page=1")
    assert calls[0][1]["Authorization"] == "Bearer ghp_secretzz" and calls[0][1]["X-GitHub-Api-Version"] == "2022-11-28"


def _fake(responses: dict, calls: list):
    def call(settings, path, params=None):
        calls.append((path, params))
        for key, value in responses.items():
            if path == key or path.startswith(key + "?"):
                return value(params) if callable(value) else value
        raise gh.GitHubError(404, {"message": f"no fake for {path}"})
    return call


def test_sync_records_commits_and_prs_by_agent_accounts_only(tmp_path):
    conn = connect(tmp_path / "t.db")
    s = github_settings(ENV)
    calls = []
    commits = lambda params: ([{"sha": "abc1234def", "html_url": "https://github.com/acme/site/commit/abc1234def",
                                "commit": {"message": "Fix checkout bug\n\nDetails", "author": {"date": "2026-09-16T10:00:00Z"}}}]
                              if params.get("author") == "acme-bot" and params.get("page", 1) == 1 else [])
    call = _fake({
        "/repos/acme/site/commits": commits,
        "/repos/acme/site/commits/abc1234def": {"files": [{"filename": f"src/f{i}.py"} for i in range(8)]},
        "/repos/acme/site/pulls": [
            {"number": 7, "title": "Add retries", "state": "open", "merged_at": None, "created_at": "2026-09-16T11:00:00Z",
             "html_url": "https://github.com/acme/site/pull/7", "user": {"login": "Acme-Bot"}},
            {"number": 6, "title": "Human PR", "state": "closed", "merged_at": "2026-09-15T00:00:00Z", "created_at": "2026-09-15T00:00:00Z",
             "html_url": "https://github.com/acme/site/pull/6", "user": {"login": "marc"}},
        ],
    }, calls)
    counts = gh.sync(conn, s, "marc", call=call)
    assert counts == {"repos": 1, "commits": 1, "pulls": 1, "new": 2}
    assert any(p == "/repos/acme/site/commits" and q.get("since") for p, q in calls)             # incremental window
    correlate(conn)
    rows = {r[0]: r for r in conn.execute("SELECT source_ref, agent, source, attribution, action_type, target, artifact_link, reversible, confidence_note FROM actions")}
    c = rows["github:commit:abc1234def"]
    assert c[1:5] == ("github (acme-bot)", "log", "agent", "file_write") and c[7] == 0
    assert c[5] == "acme/site@abc1234: Fix checkout bug — 8 files: src/f0.py, src/f1.py, src/f2.py, src/f3.py, src/f4.py, src/f5.py … +2 more"
    assert c[6] == "https://github.com/acme/site/commit/abc1234def" and "by credential: GitHub account acme-bot" in c[8]
    p = rows["github:pr:acme/site#7"]
    assert p[1:5] == ("github (Acme-Bot)", "log", "agent", "post") and p[5] == "acme/site#7: Add retries (open)" and p[7] == 1
    assert "github:pr:acme/site#6" not in rows                                                     # a person's PR: not read
    assert gh.sync(conn, s, "marc", call=call)["new"] == 0                                         # idempotent
    assert conn.execute("SELECT COUNT(*) FROM coverage WHERE source = 'log'").fetchone()[0] == 2
    assert "ghp_secret" not in json.dumps(conn.execute("SELECT raw_json, confidence_note FROM actions").fetchall())
    conn.close()


def test_repos_default_to_everything_visible():
    call = _fake({"/user/repos": [{"full_name": "acme/site"}, {"full_name": "acme/api"}]}, [])
    assert gh.repos_to_watch(github_settings({**ENV, "RECEIPT_GITHUB_REPOS": ""}), call=call) == ["acme/site", "acme/api"]
    assert gh.repos_to_watch(github_settings(ENV), call=call) == ["acme/site"]


def test_identity_display_and_stop_control():
    known = known_identities(ENV)
    assert known["github (acme-bot)"] == ("github account", "its own GitHub account, acme-bot")
    assert display("github (acme-bot)", {}, known) == {"name": "GitHub account", "sub": "acme-bot", "kind": "github account",
                                                      "raw": "github (acme-bot)", "nickname": ""}
    ids = [c["id"] for c in controls(ENV)]
    assert "github_revoke:acme-bot" in ids and "github_revoke:acme-reviewer" in ids
    c = next(c for c in controls(ENV) if c["id"] == "github_revoke:acme-bot")
    assert c["mode"] == "link" and c["url"] == "https://github.com/acme-bot" and c["effect"] == "stops the agent"
