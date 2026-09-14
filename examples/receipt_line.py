"""Append receipt lines for Agent Receipt. Copy this file into your agent.

    from receipt_line import record
    record("send_email", target="a@b.c", agent="my-bot", id="msg_1", reversible=False)

Writes one JSON line per call to ~/.agent-receipt/inbox/<agent>.jsonl.
Format: docs/RECEIPT_LINE.md. Stdlib only.
"""

import json
import os
import re
import time
from datetime import datetime, timezone

INBOX = os.environ.get("AGENT_RECEIPT_INBOX") or os.path.expanduser("~/.agent-receipt/inbox")
ACTIONS = {"send_email", "create_event", "purchase", "file_write", "post", "execute", "other"}


def record(action, target=None, agent="agent", id=None, amount=None, currency=None,
           link=None, reversible=None, reason=None, session=None, cwd=None, project=None,
           detail=None, ts=None):
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {sorted(ACTIONS)}")
    line = {
        "ts": ts or datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "agent": agent,
        "action": action,
    }
    for key, value in (("target", target), ("id", id), ("amount", amount), ("currency", currency),
                       ("link", link), ("reversible", reversible), ("reason", reason),
                       ("session", session), ("cwd", cwd), ("project", project), ("detail", detail)):
        if value is not None:
            line[key] = value
    os.makedirs(INBOX, exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", agent) or "agent"
    with open(os.path.join(INBOX, f"{name}.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return line


if __name__ == "__main__":
    print(record("other", target="self-test", agent="receipt-line-example", id=f"selftest-{int(time.time())}"))
