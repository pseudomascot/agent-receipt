#!/bin/zsh
# Builds "Agent Receipt.app" next to this file. Safe to run again.
# The app is a thin launcher: it lives in this folder and runs src/menubar.py
# with the folder's .venv (creating it on first run). No Terminal window.
cd "$(dirname "$0")" || exit 1

APP="Agent Receipt.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Agent Receipt</string>
  <key>CFBundleDisplayName</key><string>Agent Receipt</string>
  <key>CFBundleIdentifier</key><string>io.agentreceipt.app</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>CFBundleShortVersionString</key><string>0.1</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>AgentReceipt</string>
  <key>LSUIElement</key><true/>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSAppleEventsUsageDescription</key><string>Agent Receipt asks the Calendar app for events that were created, changed, or deleted. It never writes to your calendar.</string>
  <key>NSHumanReadableCopyright</key><string>MIT License</string>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/AgentReceipt" <<'LAUNCHER'
#!/bin/zsh
# Agent Receipt launcher. The app bundle sits inside the project folder.
DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
cd "$DIR" || exit 1
fail() { osascript -e "display alert \"Agent Receipt\" message \"$1\"" >/dev/null 2>&1; exit 1; }
command -v python3 >/dev/null 2>&1 || fail "Python 3 is not installed. Get it from python.org (3.11 or newer), then open Agent Receipt again."
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv || fail "Could not create the Python environment (.venv)."
  .venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt || fail "Could not install dependencies. Check your internet connection and open Agent Receipt again."
  [ -f .env ] || cp .env.example .env
fi
.venv/bin/python -c "import rumps" 2>/dev/null || .venv/bin/pip install --quiet --disable-pip-version-check -r requirements.txt
exec .venv/bin/python src/menubar.py
LAUNCHER
chmod +x "$APP/Contents/MacOS/AgentReceipt"

echo "Built $APP"
plutil -lint "$APP/Contents/Info.plist"
