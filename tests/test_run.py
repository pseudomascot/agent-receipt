import json
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dataclasses import replace

from log_parser import CLAUDE_CODE  # noqa: E402
from run import refresh  # noqa: E402
from store import connect  # noqa: E402


def _transcript(path, ts):
    entry = {
        "type": "assistant", "timestamp": ts, "sessionId": "s", "cwd": "/tmp",
        "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Write",
                                 "input": {"file_path": "/tmp/x"}}]},
    }
    path.write_text(json.dumps(entry) + "\n")


def test_refresh_parses_correlates_and_writes_summary(tmp_path):
    root = tmp_path / "projects" / "proj"
    root.mkdir(parents=True)
    _transcript(root / "s.jsonl", datetime.now().astimezone().isoformat())
    db = tmp_path / "t.db"
    out = tmp_path / "summaries"
    sources = (replace(CLAUDE_CODE, root=tmp_path / "projects"),)

    result = refresh(db, sources, out)
    assert result == {"new": 1, "removed": 0, "updated": 0, "agent": 1, "human": 0, "unknown": 0}
    assert (out / f"{date.today().isoformat()}.txt").read_text().startswith("Agent Receipt")

    conn = connect(db)
    note = conn.execute("SELECT confidence_note FROM actions").fetchone()[0]
    assert "input monitor was not running" in note
    conn.close()

    assert refresh(db, sources, out)["new"] == 0
