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


def stripe_settings(env: dict | None = None) -> dict | None:
    """Stripe API settings, or None if not configured. Test keys and live keys both work;
    the mode is shown in the agent label so a statement never mixes them up silently."""
    env = env if env is not None else load_env()
    key = env.get("RECEIPT_STRIPE_TEST_KEY") or env.get("RECEIPT_STRIPE_KEY")
    if not key:
        return None
    mode = "test" if key.startswith("sk_test_") else "live"
    return {
        "key": key,
        "mode": mode,
        "agent": env.get("RECEIPT_STRIPE_AGENT") or f"stripe card ({mode} mode)",
    }


def stripe_configured() -> bool:
    return stripe_settings() is not None


def calendar_settings(env: dict | None = None) -> dict | None:
    """macOS Calendar watching, or None unless RECEIPT_CALENDAR=on."""
    env = env if env is not None else load_env()
    if (env.get("RECEIPT_CALENDAR") or "").strip().lower() not in ("on", "1", "true", "yes"):
        return None
    names = [n.strip() for n in (env.get("RECEIPT_CALENDARS") or "").split(",") if n.strip()]
    return {"calendars": names or None, "agent": env.get("RECEIPT_CALENDAR_AGENT") or "calendar"}


def calendar_configured() -> bool:
    return calendar_settings() is not None
