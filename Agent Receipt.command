#!/bin/zsh
# Double-click to start Agent Receipt. Close the window to stop it.
cd "$(dirname "$0")" || exit 1

if [ ! -x ./.venv/bin/python ]; then
  echo "Agent Receipt isn't set up yet. Double-click \"Setup.command\" first (one time), then this again."
  echo
  printf 'Press Enter to close.'; read -r _
  exit 1
fi

exec ./.venv/bin/python src/run.py
