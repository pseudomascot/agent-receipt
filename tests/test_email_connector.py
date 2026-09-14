import email
import email.utils
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from alerts import evaluate  # noqa: E402
from card_alerts import parse_card_alert  # noqa: E402
from config import email_settings, load_env  # noqa: E402
from correlator import correlate  # noqa: E402
from email_connector import sync  # noqa: E402
from queries import coverage_notes, not_covered  # noqa: E402
from store import connect  # noqa: E402

SETTINGS = {"host": "imap.example", "user": "bot@example.com", "password": "x", "sent_folder": "[Gmail]/Sent Mail",
            "alerts_folder": "INBOX", "agent": "test-agent mailbox", "smtp_host": "s", "smtp_port": 587}


def _msg(uid, subject, to="a@b.c", body="hello\nsecond line", sender="bot@example.com", mid=None, when=None):
    when = when or time.time()
    date = email.utils.formatdate(when, localtime=True)
    raw = (f"From: {sender}\r\nTo: {to}\r\nSubject: {subject}\r\nDate: {date}\r\n"
           f"Message-ID: {mid or f'<m{uid}@example>'}\r\n\r\n").encode()
    return uid, email.message_from_bytes(raw), body


class FakeMailbox:
    def __init__(self, folders):
        self.folders = folders

    def new_messages(self, folder, after_uid):
        return [m for m in self.folders.get(folder, []) if m[0] > after_uid]

    def close(self):
        pass


@pytest.mark.parametrize("subject,body,expected", [
    ("Your $12.34 transaction with STARBUCKS", "You made a $12.34 transaction with STARBUCKS #1234\nCard ending in 4242",
     ("STARBUCKS #1234", 12.34, "USD", "4242")),
    ("Card alert", "A charge of $1,245.00 was made at AMAZON.COM on your card ending in 1004.", ("AMAZON.COM", 1245.0, "USD", "1004")),
    ("Purchase notice", "A purchase of £8.15 at TRADER JOE'S was approved.", ("TRADER JOE'S", 8.15, "GBP", None)),
    ("Transaction", "Amount: $99.00\nMerchant: ACME CLOUD\nLast four: 7788", ("ACME CLOUD", 99.0, "USD", "7788")),
    ("Newsletter", "Ten tips for better sleep. No card involved here, no transaction at all.", None),
    ("Hi", "Lunch tomorrow?", None),
])
def test_parse_card_alert(subject, body, expected):
    got = parse_card_alert(subject, body)
    if expected is None:
        assert got is None
    else:
        assert (got["merchant"], got["amount"], got["currency"], got["last4"]) == expected


def test_sync_sent_and_alerts_then_attribution(tmp_path):
    conn = connect(tmp_path / "t.db")
    now = time.time()
    box = FakeMailbox({
        "[Gmail]/Sent Mail": [_msg(1, "Invoice 1042", to="billing@client.com", when=now - 60),
                              _msg(2, "Re: hello", to="x@y.z, w@y.z", when=now - 30)],
        "INBOX": [_msg(5, "Your $150.00 transaction with ACME CLOUD SERVICES",
                       body="You made a $150.00 transaction with ACME CLOUD SERVICES\nCard ending in 4242",
                       sender="alerts@chase.com", when=now - 300),
                  _msg(6, "Newsletter", body="nothing to see", when=now - 10)],
    })
    assert sync(conn, SETTINGS, box, user="marc") == {"sent": 2, "cards": 1, "skipped": 1}
    rows = conn.execute("SELECT source, action_type, target, amount, currency, agent, source_ref, reversible "
                        "FROM actions ORDER BY id").fetchall()
    assert rows[0] == ("email", "send_email", "billing@client.com", None, None, "test-agent mailbox", "<m1@example>", 0)
    assert rows[1][2] == "x@y.z, w@y.z"
    assert rows[2] == ("card", "purchase", "ACME CLOUD SERVICES", 150.0, "USD", "test-agent mailbox", "<m5@example>", 0)
    assert "card ending 4242" in conn.execute("SELECT confidence_note FROM actions WHERE id = 3").fetchone()[0]

    # Second sync: UIDs remembered, nothing new.
    assert sync(conn, SETTINGS, box, user="marc") == {"sent": 0, "cards": 0, "skipped": 0}
    box.folders["[Gmail]/Sent Mail"].append(_msg(3, "New", to="n@y.z", when=now))
    assert sync(conn, SETTINGS, box, user="marc")["sent"] == 1

    # Attribution by evidence: monitor running, typing before message 1, an agent log before message 2.
    conn.execute("INSERT INTO coverage (source, started_at, ended_at) VALUES ('input', ?, ?)", (now - 600, now + 60))
    conn.execute("INSERT INTO input_events (timestamp, kind) VALUES (?, 'key')", (now - 65,))
    conn.execute("INSERT INTO actions (timestamp, agent, source, action_type, attribution, confidence_note) "
                 "VALUES (?, 'claude-code', 'log', 'execute', 'agent', 'agent log')", (now - 35,))
    conn.commit()
    correlate(conn)
    attributions = dict(conn.execute("SELECT source_ref, attribution FROM actions WHERE source != 'log'").fetchall())
    assert attributions["<m1@example>"] == "human"      # typing, no agent log
    assert attributions["<m2@example>"] == "agent"      # agent log within 30 s
    assert attributions["<m5@example>"] == "unknown"    # neither typing nor an agent log near the charge
    # The $150 charge trips the money rule; the unknown rows trip the unknown rule.
    rules = {(n["action_id"], n["rule"]) for n in evaluate(conn)}
    assert any(r == "money_over_threshold" for _, r in rules)
    assert any(r == "unknown_attribution" for _, r in rules)
    conn.close()


def test_env_loading_and_coverage(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text('# comment\nRECEIPT_IMAP_USER="bot@example.com"\nRECEIPT_IMAP_PASSWORD=abcd efgh\n\nJUNK\n')
    env = load_env(env_file)
    settings = email_settings(env)
    assert settings["user"] == "bot@example.com" and settings["password"] == "abcd efgh"
    assert settings["host"] == "imap.gmail.com" and settings["agent"] == "mailbox bot@example.com"
    assert email_settings({}) is None
    on = {n: s for n, s, _ in coverage_notes(True)}
    off = {n: s for n, s, _ in coverage_notes(False)}
    assert on["Email sent from the agent's mailbox"] == "covered" and off["Email sent from the agent's mailbox"] == "not covered"
    assert "Crypto wallet" in not_covered(True)
