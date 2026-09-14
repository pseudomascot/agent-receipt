import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from log_parser import ingest  # noqa: E402
from store import connect  # noqa: E402
from verify import describe, last_result, verify  # noqa: E402

TS = "2026-09-14T10:00:00.000Z"


def _entry(tool_id, path="/tmp/a"):
    return json.dumps({
        "type": "assistant", "timestamp": TS, "sessionId": "s", "cwd": "/tmp",
        "message": {"content": [{"type": "tool_use", "id": tool_id, "name": "Write", "input": {"file_path": path}}]},
    }) + "\n"


def _chunks(conn):
    return conn.execute("SELECT start_offset, end_offset FROM transcript_chunks ORDER BY start_offset").fetchall()


def test_ingest_records_chunks_and_verify_passes(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(_entry("t1"))
    conn = connect(tmp_path / "t.db")
    ingest(conn, [path])
    assert _chunks(conn) == [(0, path.stat().st_size)]

    with open(path, "a") as f:
        f.write(_entry("t2"))
    ingest(conn, [path])
    c = _chunks(conn)
    assert len(c) == 2 and c[1][0] == c[0][1] and c[1][1] == path.stat().st_size

    result = verify(conn)
    assert (result["transcripts"], result["chunks"], result["unchanged"]) == (1, 2, 1)
    assert result["modified"] == [] and result["missing"] == []
    assert last_result(conn)["unchanged"] == 1
    assert describe(result).startswith("Log integrity: checked ") and "all 1 transcripts" in describe(result)
    conn.close()


def test_verify_detects_edit_truncation_and_deletion(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(_entry("t1") + _entry("t2"))
    conn = connect(tmp_path / "t.db")
    ingest(conn, [path])

    original = path.read_bytes()
    path.write_bytes(original.replace(b"/tmp/a", b"/tmp/b", 1))          # same length, one byte range changed
    result = verify(conn)
    assert result["unchanged"] == 0 and len(result["modified"]) == 1
    assert "differ from what was read on" in result["modified"][0]["problem"]
    assert "MODIFIED" in describe(result)

    path.write_bytes(original[:20])                                        # truncated
    result = verify(conn)
    assert "shorter than when read" in result["modified"][0]["problem"]

    path.unlink()
    result = verify(conn)
    assert result["missing"] == [str(path)] and result["modified"] == []
    conn.close()


def test_shrunken_file_reingest_replaces_chunks(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(_entry("t1") + _entry("t2"))
    conn = connect(tmp_path / "t.db")
    ingest(conn, [path])
    path.write_text(_entry("t1"))                                          # rewritten shorter
    ingest(conn, [path])
    assert _chunks(conn) == [(0, path.stat().st_size)]
    assert verify(conn)["unchanged"] == 1
    conn.close()


def test_describe_without_result():
    assert describe(None) == "Log integrity: not checked yet."
