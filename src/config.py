"""Settings from a git-ignored .env file next to the project. No library needed.

Credentials are read here and passed to connectors; they are never logged,
stored in the database, or shown on the page.
"""

import os
from pathlib import Path

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = ENV_PATH) -> dict:
    values = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    for key, value in os.environ.items():
        if key.startswith("RECEIPT_"):
            values[key] = value
    return values


def email_settings(env: dict | None = None) -> dict | None:
    """IMAP settings for the agent's mailbox, or None if not configured."""
    env = env if env is not None else load_env()
    user, password = env.get("RECEIPT_IMAP_USER"), env.get("RECEIPT_IMAP_PASSWORD")
    if not user or not password:
        return None
    return {
        "host": env.get("RECEIPT_IMAP_HOST", "imap.gmail.com"),
        "user": user,
        "password": password,
        "sent_folder": env.get("RECEIPT_IMAP_SENT_FOLDER", "[Gmail]/Sent Mail"),
        "alerts_folder": env.get("RECEIPT_IMAP_ALERTS_FOLDER", "INBOX"),
        "agent": env.get("RECEIPT_EMAIL_AGENT") or f"mailbox {user}",
        "smtp_host": env.get("RECEIPT_SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(env.get("RECEIPT_SMTP_PORT", "587")),
    }


def email_configured() -> bool:
    return email_settings() is not None
