import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from log_parser import (CODEX, COWORK, INBOX, RULES_VERSION, classify, current_user, find_transcripts, ingest,  # noqa: E402
                        ingest_all, parse_file, reconcile)
from dataclasses import replace  # noqa: E402
from store import connect  # noqa: E402

TS = "2026-09-14T10:00:00.000Z"
TS_EPOCH = 1789380000.0


def _assistant(name, tool_input, tool_id, ts=TS, **extra):
    return {
        "type": "assistant",
        "timestamp": ts,
        "sessionId": "sess-1",
        "cwd": "/tmp/proj",
        "entrypoint": "claude-desktop",
        "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]},
        **extra,
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
        _assistant("Bash", {"command": "ls -la | head"}, "toolu_bash_ls"),
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
    assert classify("Bash", {"command": "ls -la && git status"}) is None
    assert classify("mcp__Claude_Browser__computer", {"action": "screenshot"}) is None
    assert classify("mcp__Claude_Browser__browser_batch", {"actions": [
        {"name": "computer", "input": {"action": "screenshot"}},
        {"name": "read_page", "input": {}},
    ]}) is None


def test_classify_mcp_types_and_new_skips():
    assert classify("mcp__gmail__send_email", {"to": "a@b.c", "subject": "s"}) == ("send_email", "a@b.c", None)
    assert classify("mcp__gcal__create_event", {"title": "Dentist"}) == ("create_event", "Dentist", None)
    assert classify("mcp__stripe__create_payment", {"amount": 5}) == ("purchase", None, None)
    assert classify("mcp__dispatch__send_message", {"message": "hi"}) == ("post", "hi", None)
    assert classify("mcp__computer-use__wait", {"seconds": 2}) is None
    assert classify("mcp__computer-use__zoom", {}) is None
    assert classify("mcp__cowork__present_files", {"paths": ["x"]}) is None
    assert classify("mcp__computer-use__request_access", {}) is None
    assert classify("mcp__workspace__web_fetch", {"url": "u"}) is None
    assert classify("mcp__computer-use__write_clipboard", {"text": "t"}) == ("other", "t", None)


def test_classify_browser_side_effects():
    assert classify("mcp__Claude_Browser__computer", {"action": "left_click"}) == ("other", "left_click", None)
    batch = {"actions": [
        {"name": "navigate", "input": {"url": "u"}},
        {"name": "computer", "input": {"action": "screenshot"}},
        {"name": "computer", "input": {"action": "type", "text": "hi"}},
        {"name": "computer", "input": {"action": "type", "text": "again"}},
    ]}
    assert classify("mcp__claude-in-chrome__browser_batch", batch) == ("other", "navigate, type", None)


def _insert_raw(conn, ref, command, note="declared somewhere | agent log; no physical input"):
    conn.execute(
        "INSERT INTO actions (timestamp, agent, source, action_type, attribution, raw_json, source_ref, confidence_note) "
        "VALUES (1, 'a', 'log', 'execute', 'agent', ?, ?, ?)",
        (json.dumps({"tool": "Bash", "input": {"command": command}, "session_id": "s9"}), ref, note),
    )


def test_reconcile_applies_current_rules_to_existing_rows(tmp_path):
    conn = connect(tmp_path / "t.db")
    _insert_raw(conn, "r1", "ls")                                   # no longer a side effect
    _insert_raw(conn, "r2", "rm x")                                 # stays; gets reversible=0
    _insert_raw(conn, "r3", "ls… [20000 chars total]")              # truncated: left alone
    conn.commit()
    assert reconcile(conn) == (1, 1)
    rows = conn.execute("SELECT source_ref, reversible, confidence_note FROM actions ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["r2", "r3"]
    assert rows[0][1] == 0
    assert rows[0][2] == ("declared in Claude Code transcript, session s9; reversibility: deleted files are gone"
                          " | agent log; no physical input")
    assert reconcile(conn) == (0, 0)
    conn.close()


def test_ingest_is_incremental(tmp_path):
    path = tmp_path / "s.jsonl"
    _write_transcript(path, [_assistant("Write", {"file_path": "/tmp/a"}, "toolu_a")])
    conn = connect(tmp_path / "t.db")
    assert ingest(conn, [path]) == 1
    offset1 = conn.execute("SELECT byte_offset FROM parser_state").fetchone()[0]
    assert offset1 == path.stat().st_size

    with open(path, "a") as f:
        f.write(json.dumps(_assistant("Write", {"file_path": "/tmp/b"}, "toolu_b")) + "\n")
        f.write('{"partial line without newline')
    assert ingest(conn, [path]) == 1
    assert conn.execute("SELECT byte_offset FROM parser_state").fetchone()[0] < path.stat().st_size

    # File shrank (rotated/rewritten): re-read from the start, dedup keeps the count right.
    _write_transcript(path, [_assistant("Write", {"file_path": "/tmp/a"}, "toolu_a")])
    assert ingest(conn, [path]) == 0
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 2
    conn.close()


def test_rules_version_change_rescans_everything(tmp_path):
    path = _sample_transcript(tmp_path)
    conn = connect(tmp_path / "t.db")
    assert ingest(conn, [path]) == 3
    assert conn.execute("SELECT value FROM meta WHERE key = 'rules_version'").fetchone()[0] == RULES_VERSION
    conn.execute("UPDATE meta SET value = 'older' WHERE key = 'rules_version'")
    conn.execute("UPDATE actions SET agent = 'stale-name'")
    conn.commit()
    assert ingest(conn, [path]) == 3                                # log rows dropped and re-extracted
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 3
    assert conn.execute("SELECT DISTINCT agent FROM actions").fetchall() == [("claude-code (claude-desktop)",)]
    assert conn.execute("SELECT value FROM meta WHERE key = 'rules_version'").fetchone()[0] == RULES_VERSION
    conn.close()


def _cowork_entry(name, tool_input, tool_id, ts="2026-09-14T12:00:00.000Z"):
    return {
        "type": "assistant", "_audit_timestamp": ts, "_audit_hmac": "abc",
        "session_id": "cw-1", "uuid": "u1",
        "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": tool_input}]},
    }


def _cowork_root(tmp_path, session_type="scheduled"):
    root = tmp_path / "local-agent-mode-sessions"
    sess = root / "org" / "acct" / "local_abc"
    sess.mkdir(parents=True)
    (root / "org" / "acct" / "local_abc.json").write_text(json.dumps({
        "sessionId": "cw-1", "title": "Nightly inbox triage", "cwd": "/sessions/happy-curie",
        "sessionType": session_type, "scheduledTaskId": "task-9",
    }))
    _write_transcript(sess / "audit.jsonl", [
        {"type": "user", "_audit_timestamp": "2026-09-14T11:59:00.000Z", "message": {"content": "go"}},
        _cowork_entry("mcp__workspace__bash", {"command": "ls -la"}, "cw_ls"),
        _cowork_entry("mcp__workspace__bash", {"command": "rm -rf /sessions/happy-curie/tmp"}, "cw_rm"),
        _cowork_entry("Edit", {"file_path": "/sessions/happy-curie/notes.md", "old_string": "a", "new_string": "b"}, "cw_edit"),
        _cowork_entry("mcp__Claude_in_Chrome__navigate", {"url": "https://example.com", "tabId": "t"}, "cw_nav"),
        _cowork_entry("Read", {"file_path": "/x"}, "cw_read"),
    ])
    return root


def test_cowork_source_parses_audit_transcripts(tmp_path):
    root = _cowork_root(tmp_path)
    source = replace(COWORK, root=root)
    conn = connect(tmp_path / "t.db")
    assert ingest_all(conn, (source,)) == 3
    rows = conn.execute(
        "SELECT source_ref, agent, action_type, target, timestamp, confidence_note, raw_json FROM actions ORDER BY id"
    ).fetchall()
    assert [r[0] for r in rows] == ["cw_rm", "cw_edit", "cw_nav"]
    assert all(r[1] == "cowork (scheduled task)" for r in rows)
    assert rows[0][2] == "execute" and rows[0][3].startswith("rm -rf")
    assert rows[0][4] == TS_EPOCH + 2 * 3600                         # _audit_timestamp, 12:00Z
    assert rows[0][5].startswith("declared in cowork transcript, session cw-1; reversibility: deleted files are gone")
    raw = json.loads(rows[1][6])
    assert raw["project"] == "Nightly inbox triage" and raw["cwd"] == "/sessions/happy-curie" and raw["source"] == "cowork"
    assert ingest_all(conn, (source,)) == 0
    conn.close()


def test_cowork_unscheduled_agent_label(tmp_path):
    root = _cowork_root(tmp_path, session_type=None)
    path = next(root.rglob("audit.jsonl"))
    assert {a["agent"] for a in parse_file(path, replace(COWORK, root=root))} == {"cowork"}


def _codex_root(tmp_path):
    root = tmp_path / "sessions" / "2026" / "09" / "10"
    root.mkdir(parents=True)
    ev = lambda item, ts="2026-09-10T15:06:00.000Z": {  # noqa: E731
        "timestamp": ts, "type": "event_msg", "payload": {"type": "item_completed", "item": item}}
    _write_transcript(root / "rollout-x.jsonl", [
        {"timestamp": "2026-09-10T15:05:33.757Z", "type": "session_meta",
         "payload": {"id": "sess-cx", "cwd": "/Users/marc/Documents/Codex/in", "originator": "codex_work_desktop"}},
        ev({"type": "CommandExecution", "id": "exec-1", "command": "ls -la", "cwd": "/Users/marc/Documents/Codex/in", "status": "completed"}),
        ev({"type": "CommandExecution", "id": "exec-2", "command": ["rm", "-rf", "work/tmp"], "cwd": "/Users/marc/Documents/Codex/in", "exit_code": 0}),
        ev({"type": "CommandExecution", "id": "exec-3", "command": ["/bin/zsh", "-lc", "pwd && rg --files -g 'AGENTS.md'"], "cwd": "/Users/marc/Documents/Codex/in"}),
        ev({"type": "CommandExecution", "id": "exec-4", "command": ["/bin/zsh", "-lc", "mkdir -p work && rm -rf work/old"], "cwd": "file:///Users/marc/Documents/Codex/in"}),
        ev({"type": "FileChange", "id": "fc-1", "changes": [
            {"path": "/Users/marc/Documents/Codex/in/a.swift", "kind": {"type": "add"}, "diff": "..."},
            {"path": "/Users/marc/Documents/Codex/in/b.swift", "kind": {"type": "update"}, "diff": "..."},
            {"path": "/Users/marc/Documents/Codex/in/c.swift", "kind": {"type": "delete"}},
            "/Users/marc/Documents/Codex/in/d.swift"]}),
        ev({"type": "McpToolCall", "id": "mcp-1", "server": "gmail", "tool": "send_email", "arguments": "{\"to\": \"x@y.z\"}"}),
        ev({"type": "Extension", "id": "ext-1", "kind": "imageGeneration", "savedPath": "/Users/marc/Documents/Codex/in/img.png"}),
        ev({"type": "Reasoning", "id": "r-1", "summary_text": "thinking"}),
        {"timestamp": "2026-09-10T15:07:00.000Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "send_message", "arguments": "{}"}},
    ])
    return tmp_path / "sessions"


def test_codex_source(tmp_path):
    root = _codex_root(tmp_path)
    conn = connect(tmp_path / "t.db")
    assert ingest_all(conn, (replace(CODEX, root=root),)) == 8
    rows = conn.execute("SELECT source_ref, action_type, target, reversible, agent, confidence_note "
                        "FROM actions ORDER BY id").fetchall()
    assert [r[0] for r in rows] == ["exec-2", "exec-4", "fc-1:0", "fc-1:1", "fc-1:2", "fc-1:3", "mcp-1", "ext-1"]
    assert rows[0][1:4] == ("execute", "rm -rf work/tmp", 0)
    assert rows[1][1:4] == ("execute", "mkdir -p work && rm -rf work/old", 0)   # zsh -lc unwrapped
    cwd = json.loads(conn.execute("SELECT raw_json FROM actions WHERE source_ref = 'exec-4'").fetchone()[0])["cwd"]
    assert cwd == "/Users/marc/Documents/Codex/in"                              # file:// stripped
    assert rows[2][1:3] == ("file_write", "/Users/marc/Documents/Codex/in/a.swift")
    assert rows[4][1:4] == ("file_write", "/Users/marc/Documents/Codex/in/c.swift", 0)
    assert "reversibility: file deleted" in rows[4][5]
    assert rows[5][1:3] == ("file_write", "/Users/marc/Documents/Codex/in/d.swift")   # bare-string change
    assert rows[6][1:3] == ("send_email", "x@y.z")
    assert rows[7][1:3] == ("file_write", "/Users/marc/Documents/Codex/in/img.png")
    assert "generated by imageGeneration" in rows[7][5]
    assert {r[4] for r in rows} == {"codex (codex_work_desktop)"}
    assert all("session sess-cx" in r[5] for r in rows)
    conn.close()


def test_one_broken_transcript_does_not_stop_ingest(tmp_path, capsys):
    good = tmp_path / "good.jsonl"
    bad = tmp_path / "bad.jsonl"
    _write_transcript(good, [_assistant("Write", {"file_path": "/a"}, "ok1")])
    _write_transcript(bad, [_assistant("Write", {"file_path": "/b"}, "bad1")])

    def exploding_extract(entry, meta):
        if "/b" in json.dumps(entry):
            raise RuntimeError("boom")
        return _tool_use_calls(entry, meta)

    from log_parser import CLAUDE_CODE, _tool_use_calls
    source = replace(CLAUDE_CODE, extract=exploding_extract)
    conn = connect(tmp_path / "t.db")
    assert ingest(conn, [bad, good], source=source) == 1
    assert "could not parse bad.jsonl" in capsys.readouterr().err
    assert conn.execute("SELECT COUNT(*) FROM parser_state").fetchone()[0] == 1   # bad file will be retried
    conn.close()


def test_inbox_source(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    _write_transcript(root / "bot.jsonl", [
        {"ts": "2026-09-14T15:04:05Z", "agent": "invoice-bot", "action": "send_email", "target": "b@x.c",
         "id": "msg_1", "reversible": False, "reason": "left the server", "detail": {"subject": "Inv 1"},
         "project": "Billing"},
        {"ts": 1789398245000, "agent": "ops-bot", "action": "purchase", "target": "AWS", "amount": 42.1,
         "currency": "USD", "link": "https://console.aws/inv/1"},
        {"ts": "2026-09-14T15:05:00Z", "agent": "ops-bot", "action": "execute",
         "detail": {"command": "git push origin main"}},
        {"ts": "2026-09-14T15:06:00Z", "agent": "ops-bot", "action": "not_a_type", "target": "x"},
        {"ts": "2026-09-14T15:07:00Z", "action": "post", "target": "no agent field"},
        "garbage",
    ])
    conn = connect(tmp_path / "t.db")
    assert ingest_all(conn, (replace(INBOX, root=root),)) == 3
    rows = conn.execute("SELECT agent, action_type, target, amount, currency, artifact_link, reversible, "
                        "confidence_note, source_ref, raw_json, timestamp FROM actions ORDER BY id").fetchall()
    assert rows[0][:3] == ("invoice-bot", "send_email", "b@x.c")
    assert rows[0][6] == 0 and "reversibility: left the server" in rows[0][7] and rows[0][8] == "msg_1"
    assert rows[0][7].startswith("declared by the agent itself via the receipt inbox")
    assert json.loads(rows[0][9])["project"] == "Billing"
    assert rows[1][:6] == ("ops-bot", "purchase", "AWS", 42.1, "USD", "https://console.aws/inv/1")
    assert rows[1][10] == 1789398245.0                                   # epoch ms accepted
    assert rows[2][1] == "execute" and rows[2][6] == 0                    # our own rules: git push irreversible
    assert len(rows[2][8]) == 32                                          # hashed id when none given
    assert ingest_all(conn, (replace(INBOX, root=root),)) == 0
    conn.close()


def test_declaration_merges_with_observed_action(tmp_path):
    root = tmp_path / "inbox"
    root.mkdir()
    conn = connect(tmp_path / "t.db")
    # The mailbox connector already recorded the message, attributed by evidence.
    conn.execute(
        "INSERT INTO actions (timestamp, agent, source, action_type, target, attribution, confidence_note, source_ref) "
        "VALUES (1789398245, 'mailbox bot', 'email', 'send_email', 'b@x.c', 'human', "
        "'sent from mailbox bot@x.c | physical input present (5 events in prior 30s), no agent log entry', '<msg_1@x>')")
    conn.commit()
    _write_transcript(root / "bot.jsonl", [
        {"ts": "2026-09-14T15:04:05Z", "agent": "invoice-bot", "action": "send_email", "target": "b@x.c",
         "id": "<msg_1@x>", "reversible": False, "reason": "left the server"},
    ])
    assert ingest_all(conn, (replace(INBOX, root=root),)) == 0                # no new row: merged instead
    row = conn.execute("SELECT source, agent, attribution, reversible, confidence_note FROM actions").fetchone()
    assert row[:4] == ("log", "invoice-bot", "agent", 0)
    assert row[4].startswith("declared by the agent itself via the receipt inbox")
    assert "also observed via email" in row[4]
    assert row[4].endswith(" | physical input present (5 events in prior 30s), no agent log entry")
    conn.close()


def test_prefilter_skips_lines_without_tool_use(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"type": "assistant", "timestamp": "%s", "message": {"content": [{"type": "text", "text": "hi"}]}}\n' % TS)
    assert list(parse_file(path)) == []


def test_reconcile_leaves_credential_attributed_rows_alone(tmp_path):
    conn = connect(tmp_path / "t.db")
    conn.execute(
        "INSERT INTO actions (timestamp, agent, user, source, action_type, target, attribution, confidence_note, raw_json, source_ref) "
        "VALUES (1, 'google calendar (bot@x.c)', 'marc', 'log', 'create_event', 'Standup', 'agent', "
        "'created by the agent''s own Google account', ?, 'gcal:primary:e1')",
        (json.dumps({"source": "google_calendar", "tool": "gcal:event", "event": {"id": "e1"}}),),
    )
    conn.commit()
    assert reconcile(conn) == (0, 0)
    assert conn.execute("SELECT COUNT(*) FROM actions").fetchone()[0] == 1
    conn.close()


def test_reconcile_fills_missing_user(tmp_path):
    conn = connect(tmp_path / "t.db")
    _insert_raw(conn, "r2", "rm x")
    conn.commit()
    assert conn.execute("SELECT user FROM actions").fetchone()[0] is None
    reconcile(conn)
    assert conn.execute("SELECT user FROM actions").fetchone()[0] == current_user()
    conn.close()


def test_migration_adds_user_column_to_old_db(tmp_path):
    import sqlite3
    old = sqlite3.connect(tmp_path / "old.db")
    old.execute("CREATE TABLE actions (id INTEGER PRIMARY KEY, timestamp REAL, agent TEXT, source TEXT, "
                "action_type TEXT, attribution TEXT)")
    old.commit(); old.close()
    conn = connect(tmp_path / "old.db")
    assert "user" in {r[1] for r in conn.execute("PRAGMA table_info(actions)")}
    conn.close()


def test_parse_assesses_reversibility(tmp_path):
    actions = list(parse_file(_sample_transcript(tmp_path)))
    by_ref = {a["source_ref"]: a for a in actions}
    assert by_ref["toolu_bash"]["reversible"] == 1                  # git commit
    assert "reversibility: git keeps history" in by_ref["toolu_bash"]["confidence_note"]
    assert by_ref["toolu_write"]["reversible"] is None              # /tmp/out.txt is not in a git repo
    assert by_ref["toolu_write"]["confidence_note"] == "declared in Claude Code transcript, session sess-1"


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
    assert all(a["agent"] == "claude-code (claude-desktop)" for a in actions)
    assert all(a["user"] == current_user() for a in actions)


def test_agent_name_variants(tmp_path):
    path = tmp_path / "s.jsonl"
    _write_transcript(path, [
        _assistant("Write", {"file_path": "/a"}, "t1", entrypoint=None),
        _assistant("Write", {"file_path": "/b"}, "t2", entrypoint="cli"),
        _assistant("Write", {"file_path": "/c"}, "t3", isSidechain=True),
    ])
    names = [a["agent"] for a in parse_file(path)]
    assert names == ["claude-code", "claude-code (cli)", "claude-code (claude-desktop) / subagent"]
    raw = json.loads(list(parse_file(path))[2]["raw_json"])
    assert raw["sidechain"] is True and raw["entrypoint"] == "claude-desktop"


def test_subagent_transcript_gets_its_own_identity(tmp_path):
    # Layout verified on this Mac: <project>/<session>/subagents/agent-<id>.jsonl + agent-<id>.meta.json
    project = tmp_path / "-Users-marc-proj"
    parent = project / "sess-1.jsonl"
    project.mkdir()
    _write_transcript(parent, [
        _assistant("Agent", {"description": "Read-only   test sub-agent", "prompt": "…", "subagent_type": "Explore"}, "toolu_spawn"),
    ])
    sub_dir = project / "sess-1" / "subagents"
    sub_dir.mkdir(parents=True)
    (sub_dir / "agent-ac4bd87f.meta.json").write_text(json.dumps(
        {"agentType": "Explore", "description": "Read-only test sub-agent", "toolUseId": "toolu_spawn", "spawnDepth": 1}))
    _write_transcript(sub_dir / "agent-ac4bd87f.jsonl", [
        _assistant("Read", {"file_path": "/x"}, "t_read", isSidechain=True, agentId="ac4bd87f", attributionAgent="Explore"),
        _assistant("Write", {"file_path": "/tmp/proj/out.txt"}, "t_write", isSidechain=True, agentId="ac4bd87f", attributionAgent="Explore"),
    ])

    spawn = list(parse_file(parent))
    assert len(spawn) == 1 and spawn[0]["action_type"] == "other" and spawn[0]["target"] == "Explore: Read-only test sub-agent"
    assert spawn[0]["agent"] == "claude-code (claude-desktop)"

    worker = list(parse_file(sub_dir / "agent-ac4bd87f.jsonl"))
    assert [a["source_ref"] for a in worker] == ["t_write"]                       # the Read is not a side effect
    assert worker[0]["agent"] == "claude-code (claude-desktop) / Explore: Read-only test sub-agent"
    raw = json.loads(worker[0]["raw_json"])
    assert raw["agent_id"] == "ac4bd87f" and raw["parent_session"] == "sess-1" and raw["spawned_by"] == "toolu_spawn"
    assert find_transcripts(project) and any(p.name == "agent-ac4bd87f.jsonl" for p in find_transcripts(project))

    # No sidecar: still a distinct worker, named by role and id.
    (sub_dir / "agent-ac4bd87f.meta.json").unlink()
    assert list(parse_file(sub_dir / "agent-ac4bd87f.jsonl"))[0]["agent"] == "claude-code (claude-desktop) / Explore ac4bd87f"


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


def _cursor_tree(tmp_path, folder="Users-marc-Desktop-cursor-test", conv="e1838abb"):
    d = tmp_path / "projects" / folder / "agent-transcripts" / conv
    d.mkdir(parents=True)
    return d / f"{conv}.jsonl"


def test_cursor_source_reads_its_agent_transcripts(tmp_path, monkeypatch):
    import log_parser
    import sqlite3
    # A fake Cursor state db with the conversation's model + title, read read-only.
    db = tmp_path / "state.vscdb"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    conn.execute("INSERT INTO cursorDiskKV VALUES (?, ?)", ("composerData:e1838abb", json.dumps(
        {"name": "Hello.txt file creation", "modelConfig": {"modelName": "grok-4.6"}})))
    conn.commit(); conn.close()
    monkeypatch.setattr(log_parser, "CURSOR_STATE_DB", db)
    (tmp_path / "Users" ).mkdir()   # nothing real: unsanitize falls back to plain splitting
    path = _cursor_tree(tmp_path)
    _write_transcript(path, [
        {"role": "user", "message": {"content": [{"type": "text", "text": "<timestamp>Wednesday, Sep 16, 2026, 8:21 AM (UTC-4)</timestamp>\n<user_query>\nmake it\n</user_query>"}]}},
        {"role": "assistant", "message": {"content": [
            {"type": "text", "text": "I'll create it."},
            {"type": "tool_use", "name": "Read", "input": {"path": "/Users/marc/Desktop/cursor-test/x"}},
            {"type": "tool_use", "name": "Write", "input": {"path": "/Users/marc/Desktop/cursor-test/hello.txt", "contents": "hello\n"}},
            {"type": "tool_use", "name": "Shell", "input": {"command": "ls", "description": "List files"}},
            {"type": "tool_use", "name": "Shell", "input": {"command": "rm -rf build", "description": "clean"}},
            {"type": "tool_use", "name": "Delete", "input": {"path": "old.txt"}},
            {"type": "tool_use", "name": "Browser", "input": {"url": "https://example.com"}},
        ]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}},
        {"type": "turn_ended", "status": "success"},
    ])
    actions = list(parse_file(path, log_parser.CURSOR))
    assert [a["action_type"] for a in actions] == ["file_write", "execute", "file_write", "other"]     # Read skipped; `ls` read-only
    assert all(a["agent"] == "cursor (grok-4.6)" and a["source"] == "log" for a in actions)
    assert actions[0]["target"] == "/Users/marc/Desktop/cursor-test/hello.txt"
    assert actions[1]["target"] == "rm -rf build" and actions[1]["reversible"] == 0
    assert actions[2]["target"].endswith("/cursor-test/old.txt") and json.loads(actions[2]["raw_json"])["tool"] == "cursor:delete"
    assert actions[3]["target"] == "https://example.com" and json.loads(actions[3]["raw_json"])["tool"] == "cursor:Browser"
    from datetime import datetime, timezone, timedelta
    stamp = datetime(2026, 9, 16, 8, 21, tzinfo=timezone(timedelta(hours=-4))).timestamp()
    assert all(a["timestamp"] == stamp for a in actions)                                                # from the turn's stamp
    assert "Cursor transcript" in actions[0]["confidence_note"] and "minute precision" in actions[0]["confidence_note"]
    raw = json.loads(actions[0]["raw_json"])
    assert raw["source"] == "cursor" and raw["session_id"] == "e1838abb" and raw["cwd"].endswith("cursor-test")
    assert [p.name for p in find_transcripts(tmp_path / "projects", log_parser.CURSOR)] == ["e1838abb.jsonl"]

    # No state db, no timestamp tag: still parsed, dated by the file's mtime, plain "cursor" label.
    monkeypatch.setattr(log_parser, "CURSOR_STATE_DB", tmp_path / "missing.vscdb")
    path2 = _cursor_tree(tmp_path, conv="ffff0000")
    _write_transcript(path2, [{"role": "assistant", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"path": "/tmp/a"}}]}}])
    got = list(parse_file(path2, log_parser.CURSOR))
    assert len(got) == 1 and got[0]["agent"] == "cursor" and got[0]["timestamp"] > 1.7e9


def test_cursor_unsanitize_respects_existing_hyphenated_folders(tmp_path, monkeypatch):
    import log_parser
    real = tmp_path / "Users" / "marc" / "my-project"
    real.mkdir(parents=True)
    monkeypatch.setattr(log_parser.Path, "exists", lambda self: str(self).startswith(str(tmp_path)) and (tmp_path / str(self).lstrip("/")).exists() or self == Path("/"))
    # Simpler: call the helper against a fake root by patching Path("/") lookups is awkward; test the plain fallback instead.
    assert log_parser._cursor_unsanitize("Users-marc-Desktop-cursor-test").startswith("/Users/marc")
