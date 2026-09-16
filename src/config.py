"""Settings from a git-ignored .env file next to the project. No library needed.

Credentials are read here and passed to connectors; they are never logged,
stored in the database, or shown on the page.
"""

import os
from pathlib import Path

# RECEIPT_ENV_FILE points the app at a different settings file (the demo uses examples/demo.env).
ENV_PATH = Path(os.environ.get("RECEIPT_ENV_FILE") or Path(__file__).resolve().parent.parent / ".env")


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
    if not user or not password or "xxxx" in password or user == "agent.mailbox@gmail.com":
        return None                                   # untouched .env.example placeholders
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
    if not key or key.endswith("..."):
        return None                                   # untouched .env.example placeholder
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


def google_calendar_settings(env: dict | None = None) -> dict | None:
    """Google Calendar API settings, or None unless an OAuth client is configured."""
    env = env if env is not None else load_env()
    client_id, secret = env.get("RECEIPT_GOOGLE_CLIENT_ID"), env.get("RECEIPT_GOOGLE_CLIENT_SECRET")
    if not client_id or not secret or "PASTE" in client_id:
        return None
    calendars = [c.strip() for c in (env.get("RECEIPT_GOOGLE_CALENDARS") or "primary").split(",") if c.strip()]
    emails = [e.strip() for e in (env.get("RECEIPT_GOOGLE_AGENT_EMAILS") or "").split(",") if e.strip()]
    return {"client_id": client_id, "client_secret": secret, "calendars": calendars or ["primary"],
            "agent_emails": emails, "agent": env.get("RECEIPT_GOOGLE_CALENDAR_AGENT") or "google calendar"}


def google_calendar_configured() -> bool:
    return google_calendar_settings() is not None


RAMP_HOSTS = {
    "sandbox": {"api": "https://demo-api.ramp.com", "app": "https://demo.ramp.com"},
    "production": {"api": "https://api.ramp.com", "app": "https://app.ramp.com"},
}
RAMP_SCOPES = "funds:read funds:write transactions:read users:read cards:read"


def ramp_settings(env: dict | None = None) -> dict | None:
    """Ramp Developer API settings (docs/RAMP.md), or None unless a client id/secret is set.

    Sandbox by default: demo-api.ramp.com moves no real money. RECEIPT_RAMP_ENV=production
    switches to the live API; the mode is part of the agent label so a statement never
    mixes them up silently."""
    env = env if env is not None else load_env()
    client_id, secret = env.get("RECEIPT_RAMP_CLIENT_ID"), env.get("RECEIPT_RAMP_CLIENT_SECRET")
    if not client_id or not secret or "PASTE" in client_id:
        return None
    mode = "production" if (env.get("RECEIPT_RAMP_ENV") or "sandbox").strip().lower() == "production" else "sandbox"
    return {
        "client_id": client_id,
        "client_secret": secret,
        "mode": mode,
        "api": RAMP_HOSTS[mode]["api"],
        "app": RAMP_HOSTS[mode]["app"],
        "user_id": (env.get("RECEIPT_RAMP_USER_ID") or "").strip() or None,
        "agent": env.get("RECEIPT_RAMP_AGENT") or f"ramp card ({mode})",
        "scopes": env.get("RECEIPT_RAMP_SCOPES") or RAMP_SCOPES,
    }


def ramp_configured() -> bool:
    return ramp_settings() is not None


def github_settings(env: dict | None = None) -> dict | None:
    """GitHub read-only token + the agents' own GitHub logins (docs/GITHUB.md), or None."""
    env = env if env is not None else load_env()
    token = (env.get("RECEIPT_GITHUB_TOKEN") or "").strip()
    if not token or "PASTE" in token or token.endswith("..."):
        return None
    logins = [l.strip().lstrip("@") for l in (env.get("RECEIPT_GITHUB_AGENT_LOGINS") or "").split(",") if l.strip()]
    repos = [r.strip() for r in (env.get("RECEIPT_GITHUB_REPOS") or "").split(",") if r.strip() and "/" in r]
    return {"token": token, "agent_logins": logins, "repos": repos}


def github_configured() -> bool:
    s = github_settings()
    return bool(s and s["agent_logins"])
