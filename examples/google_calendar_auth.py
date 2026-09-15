"""Sign the agent's Google account in to Agent Receipt (one time).

    ./.venv/bin/python examples/google_calendar_auth.py

Opens a browser to Google's consent page, catches the reply on 127.0.0.1,
and saves a refresh token to ~/.agent-receipt/google_token.json (private to
your user, never in the repo). Sign in as the AGENT's Google account — the
same one that owns the agent mailbox — so its calendar is what gets watched.
Needs RECEIPT_GOOGLE_CLIENT_ID and RECEIPT_GOOGLE_CLIENT_SECRET in .env
(docs/GOOGLE_CALENDAR.md explains where those come from).
"""

import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config import google_calendar_settings  # noqa: E402
from google_calendar import TOKEN_PATH, auth_url, exchange_code, save_token  # noqa: E402

settings = google_calendar_settings()
if not settings:
    sys.exit("Not configured: put RECEIPT_GOOGLE_CLIENT_ID and RECEIPT_GOOGLE_CLIENT_SECRET in .env first.")

PORT = 8766
REDIRECT = f"http://127.0.0.1:{PORT}/"
state = secrets.token_urlsafe(16)
result = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        if query.get("state", [None])[0] != state:
            self.send_response(400); self.end_headers(); self.wfile.write(b"Bad state; try again."); return
        if "code" in query:
            result["code"] = query["code"][0]
            body = b"<h2>Agent Receipt is connected to this Google account.</h2><p>You can close this tab.</p>"
        else:
            result["error"] = query.get("error", ["unknown"])[0]
            body = b"<h2>Google did not grant access.</h2>"
        self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)

    def log_message(self, *_):
        pass


server = HTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=server.handle_request, daemon=True).start()
url = auth_url(settings, REDIRECT, state)
print("Opening Google's sign-in page. Sign in as the agent's Google account and click Allow.")
print("If the browser does not open, paste this address into it:\n  " + url)
webbrowser.open(url)
import time  # noqa: E402
for _ in range(600):
    if result:
        break
    time.sleep(0.5)
server.server_close()
if "code" not in result:
    sys.exit(f"No authorization received ({result.get('error', 'timed out')}).")
token = exchange_code(settings, result["code"], REDIRECT)
if not token.get("refresh_token"):
    sys.exit("Google returned no refresh token. Remove Agent Receipt from the account's third-party access and run this again.")
save_token(token)
print(f"Saved to {TOKEN_PATH}. The connector will start polling on the next refresh.")
