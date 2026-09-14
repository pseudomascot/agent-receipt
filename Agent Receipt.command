#!/bin/zsh
# Double-click to start Agent Receipt. Close the window to stop it.
cd "$(dirname "$0")" || exit 1

if [ ! -x ./.venv/bin/python ]; then
  echo "The Python environment (.venv) is missing."
  echo "In Terminal, from this folder, run:"
  echo "  python3 -m venv .venv && ./.venv/bin/pip install -r requirements.txt"
  echo
  read -r "?Press Enter to close."
  exit 1
fi

exec ./.venv/bin/python src/run.py
