import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from log_parser import classify, ingest, parse_file  # noqa: E402
from store import connect  # noqa: E402

TS = "2026-09-14T10:00:00.000Z"
TS_EPOCH = 1789380000.0


def _assistant(name, tool_input, tool_id, ts=TS):
    return {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": "sess-1",
        "cwd": "/tmp/proj",
        "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]},
    }


def _write_transcript(path, entries):
    with open(path, "w") as f:
        for e in entries:
            f.write(json.dumps(e) if isinstance(e, dict) else e)
            f.write("\n")


def _sample_transcript(tmp_path):
    path = tmp_path / "sess-1.jsonl"
    _write_transcript(path, [
        {"type": "user", "timestamp": TS, "message": {"content": "hello"}},
        _assistant("Read", {"file_path": "/tmp/a.txt"}, "toolu_read"),
        _assistant("Write", {"file_path": "/tmp/out.txt", "content": "x" * 20000}, "toolu_write"),
        _assistant("Bash", {"command": "git commit -m 'hi'"}, "toolu_bash"),
        _assistant("mcp__Claude_Browser__read_page", {}, "toolu_mcp_read"),
        _assistant("mcp__Claude_Browser__navigate", {"url": "https://example.com"}, "toolu_mcp_nav"),
        _assistant("Artifact", {"action": "list"}, "toolu_art_list"),
        "this line is not json",
    ])
    return path


def test_classify_skips_reads():
    assert classify("Read", {"file_path": "/x"}) is None
    assert classify("Grep", {"pattern": "x"}) is None
    assert classify("mcp__foo__get_thing", {}) is None
    assert classify("Artifact", {"action": "read", "url": "u"}) is None
    assert classify("mcp__ccd_session__mark_chapter", {"title": "t"}) is None


def test_classify_side_effects():
    assert classify("Write", {"file_path": "/x"}) == ("file_write", "/x", "file:///x")
    assert classify("Bash", {"command": "rm x"}) == ("execute", "rm x", None)
    assert classify("Artifact", {"file_path": "p.html"}) == ("post", "p.html", None)
    assert classify("mcp__srv__navigate", {"url": "u"}) == ("other", "u", None)


def test_parse_file_extracts_only_side_effects(tmp_path):
    actions = list(parse_file(_sample_transcript(tmp_path)))
    assert [a["action_type"] for a in actions] == ["file_write", "execute", "other"]
    assert [a["source_ref"] for a in actions] == ["toolu_write", "toolu_bash", "toolu_mcp_nav"]
    assert all(a["timestamp"] == TS_EPOCH for a in actions)
    assert all(a["attribution"] == "agent" and a["source"] == "log" for a in actions)
    assert actions[0]["target"] == "/tmp/out.txt"
    assert actions[1]["target"] == "git commit -m 'hi'"


def test_raw_json_caps_long_strings(tmp_path):
    actions = list(parse_file(_sample_transcript(tmp_path)))
    raw = json.loads(actions[0]["raw_json"])
    assert len(raw["input"]["content"]) < 20000
    assert "20000 chars total" in raw["input"]["content"]
    assert raw["transcript"].endswith("sess-1.jsonl")


def test_ingest_is_idempotent(tmp_path):
    path = _sample_transcript(tmp_path)
    conn = connect(tmp_path / "t.db")
    assert ingest(conn, [path]) == 3
    assert ingest(conn, [path]) == 0
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 3
    conn.close()
