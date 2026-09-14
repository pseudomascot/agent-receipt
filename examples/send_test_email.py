"""Send a test email from the agent mailbox, to exercise the email connector.

    ./.venv/bin/python examples/send_test_email.py                 # to the mailbox itself
    ./.venv/bin/python examples/send_test_email.py you@example.com --declare

--declare also writes a receipt line (docs/RECEIPT_LINE.md), so the receipt
can attribute the send to an agent. Without it, the mailbox alone cannot say
who pressed send, and the row comes out human or unknown by evidence.
"""

import smtplib
import sys
import time
from email.message import EmailMessage
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import email_settings  # noqa: E402

settings = email_settings()
if not settings:
    sys.exit("Not configured: copy .env.example to .env and fill in RECEIPT_IMAP_USER / RECEIPT_IMAP_PASSWORD.")

args = [a for a in sys.argv[1:] if not a.startswith("--")]
to = args[0] if args else settings["user"]
message_id = f"<receipt-test-{int(time.time())}@agent-receipt>"

msg = EmailMessage()
msg["From"] = settings["user"]
msg["To"] = to
msg["Subject"] = f"Agent Receipt test {time.strftime('%Y-%m-%d %H:%M:%S')}"
msg["Message-ID"] = message_id
msg.set_content("This message was sent by examples/send_test_email.py to test the email connector.\n")

with smtplib.SMTP(settings["smtp_host"], settings["smtp_port"], timeout=30) as smtp:
    smtp.starttls()
    smtp.login(settings["user"], settings["password"])
    smtp.send_message(msg)
print(f"sent to {to} as {message_id}")

if "--declare" in sys.argv:
    from receipt_line import record
    record("send_email", target=to, agent=settings["agent"], id=message_id, reversible=False,
           reason="the message left the server", detail={"subject": msg["Subject"]})
    print("declared via receipt line")
