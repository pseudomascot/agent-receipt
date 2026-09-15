"""List the Ramp users on the configured account, so you can pick RECEIPT_RAMP_USER_ID.

Read-only. Uses the sandbox unless RECEIPT_RAMP_ENV=production. See docs/RAMP.md.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import ramp_settings  # noqa: E402
from ramp_connector import RampError, list_all, list_users  # noqa: E402

settings = ramp_settings()
if not settings:
    sys.exit("Ramp is not configured: set RECEIPT_RAMP_CLIENT_ID and RECEIPT_RAMP_CLIENT_SECRET in .env (docs/RAMP.md).")
print(f"Ramp {settings['mode']} — {settings['api']}\n")
try:
    users = list_users(settings)
    print(f"{'id':38}  {'name':28}  {'role':20}  email")
    for u in users:
        print(f"{u['id']:38}  {(u['name'] or '')[:28]:28}  {(u['role'] or '')[:20]:20}  {u['email'] or ''}")
    print(f"\n{len(users)} user(s). Put the id of the one who should hold the agents' cards into .env as RECEIPT_RAMP_USER_ID.")
    funds = list_all(settings, "/developer/v1/funds")
    print(f"{len(funds)} fund(s) on the account" + (":" if funds else "."))
    for f in funds:
        cards = ", ".join(f"•••• {c.get('last_four')}" for c in (f.get("cards") or []))
        print(f"  {f.get('id')}  {f.get('display_name')!r}  {f.get('state')}  {cards}")
except RampError as exc:
    sys.exit(f"Ramp refused: {exc}\nCheck the client id/secret, the app's scopes, and RECEIPT_RAMP_ENV.")
