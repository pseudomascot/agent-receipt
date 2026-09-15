import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from describe import describe  # noqa: E402

CWD = "/Users/marc/proj"


@pytest.mark.parametrize("cmd,expected", [
    ('cd "/Users/marc/proj" && git add . && git commit -q -m "x"', "Staged files for a git commit; committed changes to git"),
    ("git push origin main", "Pushed code to the remote repository"),
    ("rm -rf work/tmp", "Deleted tmp"),
    ("mkdir -p src tests && touch README.md", "Created the folder src, tests; created README.md"),
    ("./.venv/bin/pip install pynput", "Installed Python package pynput"),
    ("./.venv/bin/pytest tests/ -q", "Ran pytest tests/"),
    ("./.venv/bin/python src/run.py", "Ran the script run.py"),
    ("python3 -c 'print(1)'", "Ran a python3 snippet"),
    ("curl -s -o /dev/null http://127.0.0.1:8765/", "Downloaded a file from 127.0.0.1:8765"),
    ("curl -X POST -d a=b https://api.example.com/x", "Sent data to api.example.com"),
    ("pkill -f statement.py; sleep 3; open 'Agent Receipt.command'", "Stopped a running program; opened Agent Receipt.command"),
    ("sqlite3 db \"DELETE FROM t\"", "Deleted data from a database"),
    ("npm run dev", "Ran npm run dev"),
    ("a && b && c && d && e", "Ran a; ran b; ran c; and 2 more"),
    ("", "Ran a command: "),
])
def test_commands(cmd, expected):
    assert describe("execute", cmd) == expected


def test_files_and_others():
    assert describe("file_write", f"{CWD}/src/x.py", "Edit", CWD) == "Edited src/x.py"
    assert describe("file_write", f"{CWD}/new.txt", "Write", CWD) == "Wrote the file new.txt"
    assert describe("file_write", "/sessions/x/c.swift", "codex:file_delete", "/sessions/x") == "Deleted c.swift"
    assert describe("send_email", "billing@client.com") == "Sent an email to billing@client.com"
    assert describe("purchase", "ACME CLOUD", extra={"amount": 150.0, "currency": "USD"}) == "Paid 150.00 USD at ACME CLOUD"
    assert describe("purchase", "ACME", extra={"amount": 1234.5}) == "Paid 1,234.50 at ACME"
    assert describe("post", "report.pdf", "SendUserFile") == "Handed a file to the user: report.pdf"
    assert describe("other", "navigate, left_click, type", "mcp__Claude_Browser__browser_batch") == "Used the browser: opened a page, clicked, typed"
    assert describe("other", "https://example.com", "mcp__Claude_Browser__navigate") == "Opened a web page: https://example.com"
    assert describe("other", "charged Jane Client", "", extra={"amount": 420.0}) == "Moved money: charged Jane Client"
    assert describe("other", "left_click", "mcp__computer-use__left_click") == "Controlled the screen: clicked"


def test_leading_cd_is_dropped_even_when_shell_parsing_fails():
    heredoc = 'cd "/Users/marc/Desktop/Claude Business Ideas/agent-receipt" && .venv/bin/python - <<\'EOF\'\nprint("x")'
    out = describe("execute", heredoc)
    assert "python" in out and "cd " not in out and "print" not in out and out.endswith("(with an inline script)")
    sed = "cd '/tmp/p q' && sed -i '' 's/a \\/ b/c/' file.txt"
    assert "cd " not in describe("execute", sed)
    assert describe("execute", "cd /tmp && ls") in ("Ran a command: ls", "Listed files")  # whatever the rules say, no cd
