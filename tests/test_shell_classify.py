import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from shell_classify import is_read_only  # noqa: E402

READ_ONLY = [
    "ls -la",
    'cd "/Users/marc/Desktop/Claude Business Ideas/agent-receipt" && git status 2>&1 | head -20',
    "cat foo.txt | grep x | wc -l",
    "git log --oneline | head -3",
    "git diff --stat src/input_monitor.py",
    "git branch -a",
    "git remote -v",
    "git stash list",
    "find . -name '*.py' | xargs wc -l",
    "sed -n '100,103p' main.log | cut -c1-300",
    "sqlite3 agent_receipt.db \"SELECT COUNT(*) FROM actions GROUP BY kind;\"",
    "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/",
    "pgrep -fl input_monitor.py || echo 'not running'",
    "python3 --version; echo ---",
    "FOO=bar ls",
    "ps -p 1 -o pid,comm=",
    "",
]

SIDE_EFFECT = [
    "rm -rf build",
    "mkdir -p src tests",
    "echo hi > file.txt",
    "cat a >> b",
    "git commit -m 'x'",
    "git add .",
    "git branch -D old",
    "git remote add origin url",
    "git stash",
    "find . -name '*.pyc' -delete",
    "find . -name x -exec rm {} \\;",
    "sed -i '' 's/a/b/' file",
    "sqlite3 db.db \"DELETE FROM actions WHERE 1\"",
    "sqlite3 db.db \"INSERT INTO t VALUES (1)\"",
    "curl -X POST -d 'a=b' https://example.com",
    "curl -O https://example.com/file.zip",
    "curl -o out.html https://example.com",
    "python3 -c 'import os; os.remove(\"x\")'",
    "./.venv/bin/python src/run.py",
    "./.venv/bin/pip install pynput",
    "./.venv/bin/pytest tests/ -q",
    "pkill -f statement.py",
    "kill -INT 1234",
    "open 'Agent Receipt.command'",
    "chmod +x file",
    "for f in *; do echo $f; done",
    "echo $(rm x)",
    "cat <<'EOF' > f\nhi\nEOF",
    "ls | tee out.txt",
    "ls | xargs rm",
    "defaults write com.apple.finder AppleShowAllFiles YES",
    "unknowncommand --flag",
    "ls\nrm x",
    "ls & rm x",
    "(cd /tmp && rm x)",
]


@pytest.mark.parametrize("cmd", READ_ONLY)
def test_read_only(cmd):
    assert is_read_only(cmd), cmd


@pytest.mark.parametrize("cmd", SIDE_EFFECT)
def test_side_effect(cmd):
    assert not is_read_only(cmd), cmd
