#!/bin/zsh
# One-time setup for Agent Receipt. Double-click it. Safe to run again.
cd "$(dirname "$0")" || exit 1

echo "Agent Receipt — setup"
echo "====================="
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is not installed. Install it from https://www.python.org/downloads/ (3.11 or newer),"
  echo "then double-click Setup.command again."
  printf 'Press Enter to close.'; read -r _
  exit 1
fi

PYV=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python $PYV found, but 3.11 or newer is required. Install a newer one from https://www.python.org/downloads/"
  printf 'Press Enter to close.'; read -r _
  exit 1
fi
echo "Python $PYV: ok"

if [ ! -x ./.venv/bin/python ]; then
  echo "Creating a private Python environment in .venv ..."
  python3 -m venv .venv || { echo "Could not create .venv"; printf 'Press Enter to close.'; read -r _; exit 1; }
fi
echo "Installing the two dependencies (pynput, flask) into .venv ..."
./.venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt || {
  echo "Install failed. Check your internet connection and run Setup.command again."
  printf 'Press Enter to close.'; read -r _; exit 1; }
echo "Dependencies: ok"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created .env from .env.example (optional: fill it in later for email and Stripe; it stays private)."
fi

echo
./.venv/bin/python src/doctor.py
echo
echo "Next:"
echo "  1. If the report says the input monitor permission is NOT granted: System Settings →"
echo "     Privacy & Security → Accessibility → turn on Terminal. (Only timestamps are ever recorded.)"
echo "  2. Double-click \"Agent Receipt.command\" to start. Your statement opens at http://127.0.0.1:8765/"
echo "  3. Close that window to stop."
echo
printf 'Press Enter to close.'; read -r _
