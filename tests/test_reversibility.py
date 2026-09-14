import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from reversibility import assess  # noqa: E402


def cmd(command):
    return assess("execute", "Bash", {"command": command}, command, None)


@pytest.mark.parametrize("command", [
    "git commit -m x", "git add .", "mkdir -p a/b", "cp a b", "mv a b", "chmod +x f",
    "./.venv/bin/pip install pynput", "npm install", "open x.command",
    'cd "/tmp/x" && git add . && git commit -m y', "curl -s https://example.com",
    "sqlite3 db \"INSERT INTO t VALUES (1)\"", "defaults write com.x y 1",
])
def test_reversible_commands(command):
    verdict, reason = cmd(command)
    assert verdict == 1, (command, reason)
    assert reason


@pytest.mark.parametrize("command", [
    "rm -rf build", "git push origin main", "git reset --hard HEAD~1", "git branch -D old",
    "git clean -fd", "git stash drop", "curl -X POST -d a=b https://x", "npm publish",
    "sqlite3 db \"DELETE FROM t\"", "gh pr create --title x", "ssh host 'ls'",
    "mkdir x && rm -rf y",                      # worst segment wins
    "sudo rm -rf /tmp/x",
])
def test_irreversible_commands(command):
    verdict, reason = cmd(command)
    assert verdict == 0, (command, reason)
    assert reason


@pytest.mark.parametrize("command", [
    "./.venv/bin/python src/run.py", "unknown-tool --go", "pkill -f x", "mkdir a && somethingelse",
    "python3 -c 'print(1)'",
])
def test_unassessed_commands(command):
    assert cmd(command) == (None, None)


def test_file_write_in_git_repo(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    inside = str(tmp_path / "repo" / "src" / "x.py")
    outside = str(tmp_path / "elsewhere" / "x.py")
    assert assess("file_write", "Edit", {"file_path": inside}, inside, None)[0] == 1
    assert assess("file_write", "Write", {"file_path": outside}, outside, None) == (None, None)


def test_post_and_mcp_tools():
    assert assess("post", "SendUserFile", {"files": ["a"]}, "a", None)[0] == 0
    assert assess("post", "Artifact", {"file_path": "p.html"}, "p.html", None)[0] == 1
    assert assess("post", "Artifact", {"action": "delete", "url": "u"}, "u", None)[0] == 0
    assert assess("other", "mcp__Claude_Browser__preview_start", {}, None, None)[0] == 1
    assert assess("other", "mcp__Claude_Browser__computer", {"action": "left_click"}, "left_click", None) == (None, None)
